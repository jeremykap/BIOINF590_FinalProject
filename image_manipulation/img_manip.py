import sys
import os
import numpy as np
from scipy import interpolate
from scipy.ndimage import morphology
from scipy.stats import multivariate_normal
from PIL import Image, ImageDraw
from cv2 import GaussianBlur, blur, getPerspectiveTransform, warpPerspective

from hashlib import blake2s

from deconvolution import Deconvolution
import deconvolution.pixeloperations as po

def rand_spline(dim, inPts = None, nPts = 5, random_seed = None, startEdge = True, endEdge = True):
    # splXY = rand_spline(dim, inPts= None, nPts = 5, random_seed =None, startEdge = True, endEdge = True)
    #     builds a randomized spline from a set of randomized handle points
    #     
    # ###
    # Inputs: Required
    #     dim: a 2 element vector   Width by Height
    # Inputs: Optional
    #     inPts: n x 2 numpy arr    Used to prespecify the handle points of the spline
    #                               note: this is not random
    #     nPts: int                 The number of random handle points in the spline
    #     random_seed: int          The random seed for numpy for consistent generation
    #     startEdge: bool           Whether or not the start of the spline should be on the edge of the image
    #                int(0,1,2,3)   If startEdge is an int, it specifies which edge the spline starts on
    #                               0 = Left, 1 = Top, 2 = Right, 3 = Bottom
    #     endEdge: bool             Whether or not the start of the spline should be on the edge of the image
    #              int(0,1,2,3)     If endEdge is a nonnegative int, it specifies which edge the spline stops on
    #                               0 = Left, 1 = Top, 2 = Right, 3 = Bottom
    #              int(-4,-3,-2,-1) If endEdge is a negative int, it specifies which edge the spline stops on 
    #                               relative to the start
    #                               -4 = Same, -3 = End is 1 step clockwise (e.g. Bottom -> Left)
    #                               -2 = Opposite side, -1 = End is 1 step counterclockwise (e.g. Bottom -> Right)
    # ###
    # Output:
    #     splXY: m x 2 numpy array  Spline array sampled at a 1-pixel interval (distance between m points is ~1px)
    
    np.random.seed(seed=random_seed)

    invDim = (dim[1],dim[0]) # have to invert the size dim because rows cols is yx vs xy
    if inPts is None:
        inPts = np.concatenate((np.random.randint((dim[0]-1),size=(nPts,1)),
                                 np.random.randint((dim[1]-1),size=(nPts,1))),
                                axis=1)
        
        startEdgeFlag = (startEdge == True) or (startEdge in range(0,4))
        if startEdgeFlag == True:
            if (startEdge in range(0,4)) and (type(startEdge)!=bool): # allow for manual specification of edge
                edgeNum = startEdge
            else:
                edgeNum = np.random.randint(4)
            LR_v_TB = edgeNum % 2 # left/right vs top/bottom
            LT_V_RB = edgeNum // 2 # left/top vs right/bottom
            
            inPts[0,LR_v_TB] = LT_V_RB * (dim[LR_v_TB]-1) # one edge or the other
        if endEdge == True or (endEdge in range(0,4)) or (endEdge in range(-4,0) and startEdgeFlag):
            if (endEdge in range(0,4)):  # allow for manual specification of edge
                edgeNum = endEdge
            elif(endEdge in range(-4,0) and startEdgeFlag): 
                # allow for relative specification of end edge compared to the start edge
                # -2 is opposite side, -4 is the same side
                edgeNum = ((endEdge + edgeNum + 4) % 4)
            else:
                edgeNum = np.random.randint(4)
            LR_v_TB = edgeNum % 2 # left/right vs top/bottom
            LT_V_RB = edgeNum // 2 # left/top vs right/bottom    
            
            inPts[nPts-1,LR_v_TB] = LT_V_RB * (dim[LR_v_TB]-1) # one edge or the other
        
    else:
        if isinstance(inPts,list):
            inPts = np.array(inPts)
        nPts = inPts.shape[0]

    distXY = np.sqrt(np.sum(np.diff(inPts,axis=0)**2,axis=1))
    cdXY = np.concatenate((np.zeros((1)),np.cumsum(distXY)),axis=0)
    iDist = np.arange(np.floor(cdXY[-1])+1)
    splXY = interpolate.pchip_interpolate(cdXY,inPts,iDist)
    return splXY

def rand_gauss(dim, nNorms = 25, maxCov = 50,  random_seed = None,centXY = None, zeroToOne = False,
               minMaxX = None, minMaxY = None, minCovScale = .1,minDiagCovScale = .25, maxCrCovScale = .7):
    # sumMap = rand_gauss(dim, nNorms = 25, maxCov = 50, random_seed = None,centXY = None, zeroToOne = False,
    #                     minMaxX = None, minMaxY = None, minCovScale = .1,minDiagCovScale = .25, maxCrCovScale = .7):
    #          Builds a set of randomized Gaussians within a set range of properties, and adds them together
    #
    # ###
    # Inputs: Required
    #     dim: a 2 element vector   Width by Height
    # Inputs: Optional
    #     nNorms: int               The number of Gaussian distributions to generate
    #     maxCov: float (+)         The maximum covariance of each Gaussian
    #                               - Related to the size of each Gaussian distribution in pixels
    #     random_seed: int          The random seed for numpy for consistent generation
    #     centXY: n x 2 float arr   The locations of the Gaussians can be specified manually if desired, instead of randomly
    #     zeroToOne: bool           Whether or not the coordinates are scaled zeroToOne (requires retuning the other sizes)
    #     minMaxX: 2 float vector   The range of where the gaussians are generated in the image in the X dimension
    #                               - Defaults to the range of the image, could be off screen if desired
    #     minMaxY: 2 float vector   The range of where the gaussians are generated in the image in the Y dimension
    #                               - Defaults to the range of the image, could be off screen if desired
    #     minCovScale: float        The minimum on the range of sizes across the set of distributions
    #         Recommend >0 & ≤1
    #     minDiagCovScale: float    Affects the diagonal of the covariance matrix, and the minimum relative size 
    #         Recommend >0 & ≤1     of the two components compared to the scaled max covariance
    #     maxCrCovScale: float      The maximum relative cross covariance (1 = straight line, 0 = uncorrelated)
    #          Recommend >0 & ≤1    Affects the shape of the distributions, a higher number means more eccentricity
    # ###
    # Output:
    #     sumMap: numpy array (dim) Creates a numpy array of the input size, where all the scaled Gaussian 
    #                               distributions have been added together
    
    
    np.random.seed(seed=random_seed)
    invDim = (dim[1],dim[0])
     
    if zeroToOne == True:
        xV = np.linspace(0,1,num= dim[0])
        yV = np.linspace(0,1,num= dim[1])
        if minMaxX is None:
            minMaxX = [0,1]
        if minMaxY is None:
            minMaxY = [0,1]
    else:
        xV = np.arange(dim[0])
        yV = np.arange(dim[1])
        
        if minMaxX is None:
            minMaxX = [0,dim[0]]
        if minMaxY is None:
            minMaxY = [0,dim[1]]

    xM, yM = np.meshgrid(xV,yV)
    pos = np.dstack((xM, yM))
    
    if centXY is None:
        centXY = np.concatenate((np.random.uniform(minMaxX[0],minMaxX[1],size=(nNorms,1)),
                        np.random.uniform(minMaxY[0],minMaxY[1],size=(nNorms,1))), axis=1)
    else:
        nNorms = centXY.shape[0]
    
    sumMap = np.zeros(invDim)
    for i in range(nNorms):
        cent = centXY[i,:]
        cov = np.zeros((2,2))
        # need to make a symmetric positive semidefinite covariance matrix
        cMaxCov = np.random.uniform(maxCov* minCovScale,maxCov,size=(1,1))
        cov = np.diag(np.random.uniform(cMaxCov * minDiagCovScale,cMaxCov,size=(1,2)).flatten())
        maxCrCov = np.sqrt(np.product(np.diag(cov)))
        cov[[0,1],[1,0]] = np.random.uniform(-maxCrCov* maxCrCovScale,maxCrCov*maxCrCovScale) 
        rv = multivariate_normal(cent.flatten(), cov)
        
        sumMap += (rv.pdf(pos) * (cMaxCov * 2 * np.pi))

    sumMap = sumMap * maxCov
    return sumMap

def add_marker(inputIm,random_seed = None,nPts = 3, sampSpl = None, inPts = None, 
               width = 100, alpha = .75, rgbVal= None,
               rgbRange = np.array([[0,50],[0,50],[0,100]])):
    # comp_im = add_marker(inputIm,random_seed = None,nPts = 3, sampSpl = None, inPts = None, 
    #                     width = 100, alpha = .75, rgbVal= None,
    #                     rgbRange = np.array([[0,50],[0,50],[0,100]])):
    #           adds a marker line onto the image of a fixed width and color
    #
    # ###
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     random_seed: int          The random seed for numpy for consistent generation
    #     nPts: int                 The number of random handle points in the spline
    #     sampSpl: n x 2 numpy arr  You can optionally specify the sampled spline (non-random)
    #                               - Note: should be sampled densely enough (i.e. at least every pixel)
    #     inPts: n x 2 numpy arr    Used to prespecify the handle points of the spline
    #                               - Note: this is not random
    #     width: float (+)          The width of the marker line, in pixels
    #     alpha: float (0-1)        The alpha transparency of the marker layer (1 = opaque, 0 = transparent)
    #     rgbVal: 3 uint8 vector    The RGB color of the marker can be optionally specified
    #           >=0 <=255
    #     rgbRange: 3 x 2 uint8 arr The RGB range of the randomized color [[minR,maxR],[minG,maxG],[minB,maxB]]
    #           >=0 <=255           - Leans more blue heavy by default
    # ###
    # Output:
    #     comp_im: a PIL Image      A 2D RGB image with the marker layer on top of the original image
    
    
    np.random.seed(seed=random_seed)
    if rgbVal is None:
        rgbVal = np.zeros((3,1))
        for i in range(3):
            rgbVal[i] = np.random.randint(rgbRange[i,0],rgbRange[i,1])
        rgbVal = rgbVal.flatten()

    dim = inputIm.size # width by height
    invDim = (dim[1],dim[0]) # have to invert the size dim because rows cols is yx vs xy
    if sampSpl is None:
        if inPts is None:
            sampSpl = rand_spline(dim, nPts = nPts,random_seed = random_seed)
        else:
            sampSpl = rand_spline(dim, inPts = inPts,random_seed = random_seed)
        
    mask = np.ones(invDim)
    mask[(np.round(sampSpl[:,1])).astype(int),np.round(sampSpl[:,0]).astype(int)] = 0
    # create a distance map to the points on the spline
    bwDist = morphology.distance_transform_edt(mask)

    # use the distance map to build a fixed width region
    bwReg = bwDist <= width/2
    im_rgba = inputIm.convert("RGBA")
    # build up the semi-transparent colored layer
    alpha_mask = Image.fromarray((bwReg*alpha*255).astype(np.uint8),'L')
    color_arr = np.zeros((invDim[0],invDim[1],3),dtype=np.uint8)
    for i in range(len(rgbVal)):
        color_arr[:,:,i] = rgbVal[i]
    color_layer = Image.fromarray(color_arr,'RGB')

    comp_im = Image.composite(color_layer, im_rgba, alpha_mask)
    comp_im = comp_im.convert("RGB")
    return comp_im


def add_fold(inputIm, sampArr = None, sampSpl=None, inPts = None, random_seed =None, scaleXY =[1,1], width = 200,
             sampShiftXY = None, randEdge=False, nLayers = 2, nPts = 3,endEdge = -2):
    # comp_im = add_fold(inputIm,samp_arr =None, sampSpl=None, inPts = None,random_seed =None,scaleXY =[1,1], width = 200,
    #                    sampShiftXY = None,randEdge=False, nLayers = 2, nPts = 3,endEdge = -2):
    #           adds a tissue fold to the input image along a spline path
    #           based on sampling from the input image
    #
    # ###
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     sampArr: numpy arr        A numpy array the size of the input image
    #     - used for recursion      - if the input image is not where the tissue should be sampled from
    #     sampSpl: n x 2 numpy arr  You can optionally specify the sampled spline (non-random)
    #     - used for recursion      
    #     inPts: n x 2 numpy arr    Used to prespecify the handle points of the spline
    #                               - Note: this is not random
    #     random_seed: int          The random seed for numpy for consistent generation
    #     scaleXY: 2 float vector   Used to scale the sampling bounding box, if the sample region should be resized
    #                               Defaults to no change between original and sampling
    #                               large scale = larger sample region
    #     width: float (+)          The width of the tissue fold region, in pixels
    #     sampShiftXY: 2 int vec    You can optionally specify the direction to shift the spline region
    #                               Defaults to a random direction at most half the size of the image
    #     randEdge: bool            Whether to add some randomness to the edge of the tissue fold region
    #                               Defaults to off
    #     nLayers: int (+)          Number of tissue layers to add to the image
    #                               Runs the function recursively, defaults to 2 layers
    #     nPts: int (+)             The number of random handle points in the spline
    #     endEdge: bool             Whether or not the start of the spline should be on the edge of the image
    #              int(0,1,2,3)     If endEdge is a nonnegative int, it specifies which edge the spline stops on
    #                               0 = Left, 1 = Top, 2 = Right, 3 = Bottom
    #              int(-4,-3,-2,-1) If endEdge is a negative int, it specifies which edge the spline stops on 
    #                               relative to the start
    #                               -4 = Same, -3 = End is 1 step clockwise (e.g. Bottom -> Left)
    #                               -2 = Opposite side, -1 = End is 1 step counterclockwise (e.g. Bottom -> Right)
    #                               Defaults to -2
    # ###
    # Output:
    #     comp_im: a PIL Image      A 2D RGB image with the tissue fold layers on top of the original image
    
    np.random.seed(seed=random_seed)
    if nLayers < 1: # if someone handed in an invalid # of layers, return back the original image
        return inputIm
    
    im_arr = np.array(inputIm)
    dim = inputIm.size # width by height
    invDim = (dim[1],dim[0]) # have to invert the size dim because rows cols is yx vs xy

    if sampSpl is None:
        if inPts is None:
            sampSpl = rand_spline(dim, nPts = nPts,random_seed = random_seed, endEdge = endEdge)
        else:
            sampSpl = rand_spline(dim, inPts = inPts,random_seed = random_seed)
    
    if sampArr is None:
        sampArr = np.copy(im_arr)
        
    if sampShiftXY is None: # randomly initialized if empty
        shiftXY = np.random.randint(-int(dim[0]/2),int(dim[0]/2),size=(2,1))
    else:
        shiftXY = sampShiftXY
            

    pad_szXY = (max(dim),max(dim),0) # pad x, pad y, no pad z (have to reshape for np.pad, which takes y,x,z)
    sampBlur = (((width//40)*2)+1,((width//40)*2)+1) # has to be odd kernel
    
    # pad the array to allow for sampling, mirror tiles outside of range
    pad_amt = np.transpose(np.tile(np.array(pad_szXY)[[1,0,2]],(2,1)))
    sampPadArr = np.pad(sampArr,pad_amt,mode='symmetric')

    sampSplBBox = np.vstack((np.amin(sampSpl,axis=0),np.amax(sampSpl,axis=0)))
    sampSplBBSz = np.diff(sampSplBBox,axis=0)
    rsSplBBox = np.zeros((2,2))

    # build up the bounding box of the region to be sampled from
    signTup = (-1,1)
    for di in range(2):
        rsSplBBox[di,:] = np.mean(sampSplBBox,axis=0) + (((sampSplBBSz/2) * scaleXY) * signTup[di])
        
    rsSplBBSz = np.diff(rsSplBBox,axis=0)

    # allow for a random shift to the sampling region, to each of the corners of the sample region
    sampSplBBPts = np.zeros((4,2),dtype=np.float32)
    outBBPts = np.zeros((4,2),dtype=np.float32)
    # maximum change is ± 1/4 of the size of the sampling bounding box 
    randShiftX = np.random.randint(-int(rsSplBBSz[0,0]/4),int(rsSplBBSz[0,0]/4),size=(4,1))
    randShiftY = np.random.randint(-int(rsSplBBSz[0,1]/4),int(rsSplBBSz[0,1]/4),size=(4,1))
    
    for di in range(sampSplBBPts.shape[0]):    
        LR_v_TB = di % 2 # left/right vs top/bottom
        LT_V_RB = di // 2 # left/top vs right/bottom
        sampSplBBPts[di,0] = sampSplBBox[LR_v_TB,0]
        sampSplBBPts[di,1] = sampSplBBox[LT_V_RB,1]
        outBBPts[di,0] = rsSplBBox[LR_v_TB,0] + pad_szXY[0] + shiftXY[0] + randShiftX[di]
        outBBPts[di,1] = rsSplBBox[LT_V_RB,1] + pad_szXY[1] + shiftXY[1] + randShiftY[di]

    # generate mapping matrix from original bounding box to the sampled bounding box
    M = getPerspectiveTransform(outBBPts,sampSplBBPts)
    # warp the padded array based on this transform
    warp_im = warpPerspective(sampPadArr,M,dim)

    # find the distance to the spline
    mask = np.ones(invDim)
    mask[(sampSpl[:,1].astype(int)),sampSpl[:,0].astype(int)] = 0
    bwDist = morphology.distance_transform_edt(mask)
    if randEdge == True:
        distRand = np.random.randint(-int(width/4),int(width/4),size=invDim)
        bwDist = blur(bwDist+distRand,(5,5))
    
    im_L =  inputIm.convert("L")
    im_L_arr = np.array(im_L)
    bwReg = bwDist <= width/2
    

    # multiplicative combination.  Makes things darker
    unit_dst_arr = np.ones(warp_im.shape)
    for i in range(warp_im.shape[2]):
        unit_dst_arr[:,:,i] = np.where(bwReg,warp_im[:,:,i]/255,1)
    unit_dst_arr = GaussianBlur(unit_dst_arr,sampBlur,0)
    comp_arr = unit_dst_arr * im_arr
    comp_im = Image.fromarray(comp_arr.astype(np.uint8),'RGB')
    
    if nLayers > 1: # recursive addition
        comp_im = add_fold(comp_im,sampArr=sampArr, sampSpl=sampSpl,inPts=inPts,random_seed = random_seed+1,
                 scaleXY=scaleXY,width=width,sampShiftXY=sampShiftXY,randEdge=randEdge,
                 nLayers=nLayers-1)
    return comp_im

def add_sectioning(inputIm, width = 240, random_seed = None, scaleMin = .5, scaleMax = .8, randEdge = True,
                   sampSpl = None, inPts = None, nPts = 2, endEdge = -2):
    # comp_im = add_sectioning(inputIm, sliceWidth = 120, random_seed = None, scaleMin = .5, scaleMax = .8, randEdge = True,
    #                         sampSpl = None, inPts = None, nPts = 2, endEdge = -2):
    #         Add a region of uneven (thinner) sectioning due to different thicknesses of slide
    #         Saturation of the region is decreased by a randomized factor within a range
    #         Value of the region is increased by half of the percentage change of the saturation
    #
    # ###
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     width: float              The width of the sectioning region, in pixels
    #     random_seed: int          The random seed for numpy for consistent generation
    #     scaleMin: float           The minimum level of saturation allowed at random
    #                               -Note: this scales based off of the distance from the spline
    #     scaleMax: float           The maximum level of saturtation allowed at random
    #                               -Note: this scales based off of the distance from the spline
    #     randEdge: bool            Whether to add some randomness to the edge of the sectioning region
    #                               Defaults to on
    #     sampSpl: n x 2 numpy arr  You can optionally specify the sampled spline (non-random)
    #     - used for recursion      
    #     inPts: n x 2 numpy arr    Used to prespecify the handle points of the spline
    #                               - Note: this is not random
    #     nPts: int                 The number of random handle points in the spline
    #                               Defaults to 2
    #     endEdge: bool             Whether or not the start of the spline should be on the edge of the image
    #              int(0,1,2,3)     If endEdge is a nonnegative int, it specifies which edge the spline stops on
    #                               0 = Left, 1 = Top, 2 = Right, 3 = Bottom
    #              int(-4,-3,-2,-1) If endEdge is a negative int, it specifies which edge the spline stops on 
    #                               relative to the start
    #                               -4 = Same, -3 = End is 1 step clockwise (e.g. Bottom -> Left)
    #                               -2 = Opposite side, -1 = End is 1 step counterclockwise (e.g. Bottom -> Right)
    #                               Defaults to -2
    # ###
    # Output:
    #     comp_im: a PIL Image      A 2D RGB image with the sectioning artifact applied to the original image
    
    np.random.seed(seed=random_seed)
    dim = inputIm.size # width by height
    invDim = (dim[1],dim[0]) # have to invert the size dim because rows cols is yx vs xy
    
    if sampSpl is None:
        if inPts is None:
            sampSpl = rand_spline(dim, inPts = inPts,random_seed = random_seed)
        else:
            sampSpl = rand_spline(dim, nPts = nPts, endEdge = endEdge, random_seed = random_seed)

    mask = np.ones(invDim)
    mask[(sampSpl[:,1].astype(int)),sampSpl[:,0].astype(int)] = 0
    bw_dist = morphology.distance_transform_edt(mask)
    if randEdge == True:
        distRand = np.random.randint(-int(width/2),int(width/2),size=invDim)
        bw_dist = blur(bw_dist+distRand,(5,5))

    # scale the distance map from the randomized min to max, sectioning effect is stronger in the center
    bw_reg = bw_dist <= width/2
    nDistRng = bw_dist / (width/2)
    halfScale = (scaleMin + scaleMax)/2
    scaleRandMin = np.random.uniform(scaleMin,(halfScale+scaleMin)/2,size=(1,1))
    scaleRandMax = np.random.uniform((halfScale+scaleMax)/2,scaleMax,size=(1,1))
    scaleRMinMax = np.concatenate((scaleRandMin,scaleRandMax),axis = 1).flatten()

    nDistRng = np.interp(nDistRng,np.array([0,1],dtype=np.float64),scaleRMinMax)

    nDistRng[np.logical_not(bw_reg)] = 1

    imHSV = inputIm.convert("HSV")
    imHSV_arr = np.array(imHSV)
    # increase the lightness by half the factor of decreased saturation
    imHSV_arr[:,:,1] = np.minimum(255,np.multiply(imHSV_arr[:,:,1],nDistRng))
    imHSV_arr[:,:,2] = np.minimum(255,np.divide(imHSV_arr[:,:,2],(nDistRng+1)/2))
    # minimum function is to stop integer overflow
    imSatHSV = Image.fromarray(imHSV_arr,"HSV")
    comp_im = imSatHSV.convert("RGB")
    return comp_im

def add_bubbles(inputIm,random_seed = None,nBubbles = 25, maxWidth = 50,alpha = .75, edgeWidth = 2,
                edgeColorMult = (.75,.75,.75), rgbVal = (225,225,225)):
    # comp_im = add_bubbles(inputIm,random_seed = None,nBubbles = 25, maxWidth = 50,alpha = .75, edgeWidth = 2,
    #                      edgeColorMult = (.75,.75,.75), rgbVal = (225,225,225)):
    #           adds bubbles in the mold of nuclear bubbling randomly throughout the image
    # 
    # ###
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     random_seed: int          The random seed for numpy for consistent generation
    #     nBubbles: int (+)         The number of bubbles to generate in the image
    #     maxWidth: float (+)       The maximum width of the randomized bubbles (roughly), in pixels
    #     alpha: float (0-1)        The alpha transparency of the bubble layer (1 = opaque, 0 = transparent)
    #     edgeWidth: float (+)      The width of the darker edge of the bubble, in pixels
    #     edgeColorMult:            The RGB multiplier of the edge of the bubble 
    #        3 float vector         -Relative to the mean RGB color of the image
    #     rgbVal: 3 float vector    The RGB color of the bubbles
    # ###
    # Output:
    #     comp_im: a PIL Image      A 2D RGB image with the bubbles added to the original image
    
    np.random.seed(seed=random_seed)
    dim = inputIm.size # width by height
    invDim = (dim[1],dim[0]) # have to invert the size dim because rows cols is yx vs xy
    
    # use the randomized gaussian function
    sumMap = rand_gauss(dim,random_seed = random_seed, nNorms=nBubbles, maxCov = maxWidth, zeroToOne = False,
                       minCovScale = .1,minDiagCovScale = .25, maxCrCovScale = .7)
    
    bwReg = sumMap >= 1
    bwDist = morphology.distance_transform_edt(bwReg)
    edgeArea = np.logical_and(bwDist <= edgeWidth,bwReg)

    alphaMask = Image.fromarray((bwReg*alpha*255).astype(np.uint8),'L')
    colorArr = np.zeros((invDim[0],invDim[1],3),dtype=np.uint8)

    # set the colors for the bubbles & edges
    meanColor = np.mean(np.array(inputIm),axis=(0,1))
    for i in range(len(rgbVal)):
        colorArr[:,:,i] = rgbVal[i]
        colorArr[edgeArea,i] = np.uint8(meanColor[i] * edgeColorMult[i])

    color_layer = Image.fromarray(colorArr,'RGB')
    comp_im = Image.composite(color_layer, inputIm, alphaMask)
    return comp_im

def add_illumination(inputIm,random_seed = None, maxCov = 15, nNorms = 3,scaleMin = .8,scaleMax = 1.1,
                    minCovScale = .5,minDiagCovScale = .1, maxCrCovScale = .2):
    # comp_im = add_illumination(inputIm,random_seed = None, maxCov = 15, nNorms = 3,scaleMin = .8,scaleMax = 1.1,
    #                           minCovScale = .5,minDiagCovScale = .1, maxCrCovScale = .2):
    #           add uneven illumination artifact to the input image
    # 
    # ###
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     random_seed: int          The random seed for numpy for consistent generation
    #     maxCov: float (+)         The maximum covariance (governs the size of the distributions)
    #     nNorms: int (+)           The number of Gaussian distributions used to build the uneven illumination
    #     scaleMin: float (<1)      The minimum for the random factor used to adjust the illumination
    #     scaleMax: float (>1)      The maximum for the random factor used to adjust the illumination
    #     minCovScale: float        The minimum on the range of sizes across the set of distributions
    #         Recommend >0 & ≤1
    #     minDiagCovScale: float    Affects the diagonal of the covariance matrix, and the minimum relative size 
    #         Recommend >0 & ≤1     of the two components compared to the scaled max covariance
    #     maxCrCovScale: float      The maximum relative cross covariance (1 = straight line, 0 = uncorrelated)
    #          Recommend >0 & ≤1    Affects the shape of the distributions, a higher number means more eccentricity
    # ###
    # Output:
    #     comp_im: a PIL Image      A 2D RGB image with the uneven illumination added to the original image
    
    np.random.seed(seed=random_seed)
    dim = inputIm.size # width by height
    invDim = (dim[1],dim[0])

    xV = np.linspace(0,1,num= dim[0])
    yV = np.linspace(0,1,num= dim[1])
    xM, yM = np.meshgrid(xV,yV)
    pos = np.dstack((xM, yM))
    
    sumMap = rand_gauss(dim,random_seed = random_seed, nNorms=nNorms, maxCov = maxCov, 
                        zeroToOne = True, minMaxX = [-.5,1.5],minMaxY = [-.5,1.5],
                        minCovScale = minCovScale,minDiagCovScale = minDiagCovScale, maxCrCovScale = maxCrCovScale)

    nSumMap = (sumMap - np.amin(sumMap,axis=(0,1)))
    
    divFac = np.amax(nSumMap,axis=(0,1))
    nSumMap = nSumMap /divFac
    
    scaleRandMin = np.random.uniform(scaleMin,(1+scaleMin)/2,size=(1,1))
    scaleRandMax = np.random.uniform((1+scaleMax)/2,scaleMax,size=(1,1))
    scaleRMinMax = np.concatenate((scaleRandMin,scaleRandMax),axis = 1).flatten()
    nSumMap = np.interp(nSumMap,np.array([0,1],dtype=np.float64),scaleRMinMax)
    
    imHSV = inputIm.convert("HSV")
    imHSV_arr = np.array(imHSV)
    imHSV_arr[:,:,2] = np.minimum(255,np.multiply(imHSV_arr[:,:,2],nSumMap))
    imLumHSV = Image.fromarray(imHSV_arr,"HSV")
    comp_im = imLumHSV.convert("RGB")
    return comp_im

def adjust_stain(inputIm,adjFactor = [1,1,1]):
    # (rgbOut,rgb1,rgb2,rgb3) = adjust_stain(inputIm,adjFactor = [1,1,1])
    #           adjust the stain levels of the H&E image
    #           based on the Deconvolution package: 
    #           https://deconvolution.readthedocs.io/en/latest/readme.html#two-stain-deconvolution 
    #
    # ###
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     adjFactor: 3 float vec    The adjustment factor for each of the three basis vectors 
    #                               (<1 = less stain, 1 = same, >1 = more stain)
    #                               Element 1: Eosin
    #                               Element 2: Hematoxylin
    #                               Element 3: Null (the remaining structure)
    # ###
    # Outputs:
    #     rgbOut: m x n x 3 array   A 2D RGB image (H&E) with the stain levels adjusted
    #     rgb1: m x n x 3 numpy arr A 2D RGB image of the Eosin layer only
    #     rgb2: m x n x 3 numpy arr A 2D RGB image of the Hematoxylin layer only
    #     rgb3: m x n x 3 numpy arr A 2D RGB image of the null layer only
    
    dim = inputIm.size # width by height
    invDim = (dim[1],dim[0]) # have to invert the size dim because rows cols is yx vs xy
    iDimRGB = (invDim[0],invDim[1],3)
    stain_dict = {'eosin':[0.91, 0.38, 0.71], 'null': [0.0, 0.0, 0.0],
              'hematoxylin': [0.39, 0.47, 0.85]}
    
    ## https://deconvolution.readthedocs.io/en/latest/readme.html#two-stain-deconvolution
#     dec = Deconvolution(image=inputIm, basis=[[0.91, 0.38, 0.71], [0.39, 0.47, 0.85],[0.0, 0.0, 0.0]])
    dec = Deconvolution(image=inputIm, basis=[stain_dict['eosin'], stain_dict['hematoxylin'],stain_dict['null']])

    ## this section is extracted from the deconvolution package, but adjusted to allow for altering the stain levels
    pxO= dec.pixel_operations
    _white255 = np.array([255, 255, 255], dtype=float)
    
    v, u, w = pxO.get_basis()
    vf, uf, wf = np.zeros(iDimRGB), np.zeros(iDimRGB), np.zeros(iDimRGB)
    vf[:], uf[:], wf[:] = v, u, w
    
    # Produce density matrices for both colors + null. Be aware, as Beer's law do not always hold.
    a, b, c = map(po._array_positive, dec.out_scalars())
    af = np.repeat(a, 3).reshape(iDimRGB) * adjFactor[0] # Adjusting the exponential coefficient
    bf = np.repeat(b, 3).reshape(iDimRGB) * adjFactor[1] # For the different stain components
    cf = np.repeat(c, 3).reshape(iDimRGB) * adjFactor[2]

    # exponential map, for changing stain levels into RGB
    rgbOut = po._array_to_colour_255(_white255 * (vf ** af) * (uf ** bf) * (wf ** cf))
    rgb1 = po._array_to_colour_255(_white255 * (vf ** af))
    rgb2 = po._array_to_colour_255(_white255 * (uf ** bf))
    rgb3 = po._array_to_colour_255(_white255 * (wf ** cf))
    
    return rgbOut,rgb1,rgb2,rgb3

def add_stain(inputIm,adjFactor = None,scaleMax = [3,3,1.5], scaleMin = [1.25,1.25,1],random_seed = None):
    # comp_im = add_stain(inputIm,adjFactor = None,scaleMax = [3,3,1.5], scaleMin = [1.25,1.25,1],random_seed = None):
    #           randomly adjust the stain levels of the H&E image
    #           based on the Deconvolution package: 
    #           https://deconvolution.readthedocs.io/en/latest/readme.html#two-stain-deconvolution 
    #
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     adjFactor: 3 float vec    The adjustment factor for each of the three basis vectors 
    #                               (<1 = less stain, 1 = same, >1 = more stain)
    #                               Element 1: Eosin
    #                               Element 2: Hematoxylin
    #                               Element 3: Null (the remaining structure)
    #                               If set the change won't be random
    #     scaleMax: 3 float vector  The maximum amount of change (increase or decrease) to the stain levels
    #              (>=1)
    #     scaleMin: 3 float vector  The minimum amount of change (increase or decrease) to the stain levels
    #              (>=1)
    #     random_seed: int          The random seed for numpy for consistent generation
    # ###
    # Output:
    #     comp_im: a PIL Image      A 2D RGB image (H&E) with the stain levels adjusted
    
    
    if adjFactor is None:
        np.random.seed(seed=random_seed) 
        adjFactor = np.ones((1,3))
        for stI in range(len(scaleMax)):
            adjFactor[0,stI] = np.random.uniform(scaleMin[stI],scaleMax[stI]) ** np.random.choice((-1,1))
        adjFactor = adjFactor.flatten().tolist()
    rgbOut,rgb1,rgb2,rgb3 = adjust_stain(inputIm,adjFactor = adjFactor)
    comp_im = Image.fromarray(rgbOut,'RGB')
    return comp_im


def add_tear(inputIm,sampSpl = None, random_seed = None, nPts = 2,
             minSpacing = 20, maxSpacing = 40, tearStartFactor = [-.15,.15],tearEndFactor = [.85,1.15],
             dirMin = 10, dirMax = 30, inLineMax = None, perpMax = None, ptRadius = 2.25, tearAlpha = 1,
             inLinePercs = np.array([(-.5,-.3,-.2),(.5,.3,.2)]),perpPercs = np.array([(-.5,-.3,-.2),(.5,.3,.2)]),
             l1MinCt = 3, l1MaxCt = 8, minDensity = [.5,.5], maxDensity = [1.5,1.5],
             edgeWidth = 2, edgeAlpha = .75, edgeColorMult = [.85,.7,.85],rgbVal = (245,245,245),
             randEdge = True):
    # comp_im = add_tear(inputIm,sampSpl = None, random_seed = None, nPts = 2,
    #              minSpacing = 20, maxSpacing = 40, tearStartFactor = [-.15,.15],tearEndFactor = [.85,1.15]
    #              dirMin = 10, dirMax = 30, inLineMax = None, perpMax = None, ptRadius = 2.25, tearAlpha = 1,
    #              inLinePercs = np.array([(-.5,-.3,-.2),(.5,.3,.2)]),perpPercs = np.array([(-.5,-.3,-.2),(.5,.3,.2)]),
    #              t1MinCt = 3, t1MaxCt = 8, minDensity = [.5,.5], maxDensity = [1.5,1.5],
    #              edgeWidth = 2, edgeAlpha = .75, edgeColorMult = [.85,.7,.85], rgbVal = (245,245,245),
    #              randEdge = True):
    #           Adds a tear to the tissue as an artifact.
    #           These tears are seeded along a spline, with a randomized distance between the center of each tear.
    #           Each tear is built up in layers (3 by default), with a randomized uniform distribution at each level.
    #           The first layer has a small number of points, with a larger percentage of distance between them.
    #           The next layer uses the previous layers points as a starting point at random
    #           then adds a smaller amount of distance in a uniform distribution
    #           This is repeated again for each of the remaining layers.
    #           The result is then used to feed a distance function, so that any pixel within a radius of any of the points
    #           is added to the tear mask
    #           
    # ### 
    # Inputs: Required
    #     inputIm: a PIL Image      A 2D RGB image
    # Inputs: Optional
    #     sampSpl: n x 2 numpy arr  You can optionally specify the sampled spline (non-random)
    #     random_seed: int          The random seed for numpy for consistent generation
    #     nPts: int (+)             The number of random handle points in the spline
    #     minSpacing: float (+)     The minimum for the random spacing between tears, in pixels
    #     maxSpacing: float (+)     The maximum for the random spacing between tears, in pixels
    #     tearStartFactor:          The min for where the tear randomly starts along the spline, in percentage & 
    #       2 float vec             The max for where the tear randomly starts along the spline, in percentage
    #     tearEndFactor:            The min for where the tear randomly end along the spline, in percentage & 
    #       2 float vec             The max for where the tear randomly ends along the spline, in percentage
    #     dirMin:  float (+)        The minimum for randomized inline and perpendicular direction max distance in pixels
    #     dirMax:  float (+)        The maximum for randomized inline and perpendicular direction max distance in pixels
    #     inLineMax:  float (+)     You can optionally set the maximum size of the tear in the spline direction
    #     perpMax:  float (+)       You can optionally set the maximum size of the tear in the perpendicular direction
    #     ptRadius: float (+)       The size of each point's effect on the tear in pixels
    #     tearAlpha: float (0-1)    The alpha transparency of the tear layer (1 = opaque, 0 = transparent)
    #
    #     inLinePercs:              You can optionally set your own tear layer structure
    #       2 x n numpy float arr   The values are the percentage of distance in the in line direction that the tears take up
    #       n = number of layers    [.5,.3,.2] means most of the structure of the tear is set early
    #       rec. [[-,-,-],[+,+,+]]  and later layers fill it out
    #       rec. each row should    Making the matrix asymmetric betw. the + and -, could give a force component to the tear
    #          add up to 1 or -1    Should match the number of layers in the perpendicular side
    #                               
    #     perpPercs:                You can optionally set your own tear layer structure
    #       2 x n numpy float arr   The values are the % of distance in the perpendicular direction that the tears take up
    #       n = number of layers    [.5,.3,.2] means most of the structure of the tear is set early
    #       rec. [[-,-,-],[+,+,+]]  and later layers fill it out
    #       rec. each row should    Making the matrix asymmetric betw. the + and -, could give a sided-ness to the tear
    #          add up to 1 or -1    Should match the number of layers in the inline side
    #                               
    #     l1MinCt: int (+)          The first layer is set by number instead of density in the later layers
    #                               - Minimum # of pts in the first layer
    #     l1MaxCt: int (+)          The first layer is set by number instead of density in the later layers
    #                               - Maximum # of pts in the first layer
    #     minDensity:               The second layer and beyond are set by density instead of #
    #        n-1 int (+) vector     - Minimum density of points in the 2nd, 3rd, etc. layers
    #        n = number of layers
    #     maxDensity:               The second layer and beyond are set by density instead of #
    #        n-1 int (+) vector     - Minimum density of points in the 2nd, 3rd, etc. layers
    #     edgeAlpha: float (0-1)    The alpha transparency of the edge layer (1 = opaque, 0 = transparent)
    #     edgeColorMult:            The RGB multiplier of the edge of the tear 
    #        3 float vector         -Relative to the mean RGB color of the image
    #     rgbVal: 3 float vector    The RGB color of the tear (i.e. background)
    #     randEdge: bool            Whether to add some randomness to the edge of the tear
    #                               Defaults to on
    # ###
    # Output:
    #     comp_im: a PIL Image      A 2D RGB image with tear artifact added
    
    np.random.seed(seed=random_seed)
    dim = inputIm.size # width by height
    invDim = (dim[1],dim[0])
    if sampSpl is None:
        sampSpl = rand_spline(dim, nPts = nPts,random_seed = random_seed,endEdge=-2)
    
    # determine where the tears are located
    tearSpacing = np.random.uniform(minSpacing,maxSpacing,size=(sampSpl.shape[0],1))
    splLen = sampSpl.shape[0]-1
    minTearStartPx = splLen * 0
    maxTearEndPx = splLen * 1
    # randomly trim the start and end
    tearStEnd = np.zeros((2,1))
    tearStEnd[0] = np.random.uniform(tearStartFactor[0],tearStartFactor[1],size=(1,1)) * splLen
    tearStEnd[1] = np.random.uniform(tearEndFactor[0],tearEndFactor[1],size=(1,1)) * splLen
    tearStEnd = (np.round(tearStEnd)).astype(int)


    tearStEnd[tearStEnd > maxTearEndPx] = maxTearEndPx
    tearStEnd[tearStEnd < minTearStartPx] = minTearStartPx
    cdTS = np.round(np.cumsum(tearSpacing)).astype(int)
    cdTS = cdTS[(cdTS >= tearStEnd[0]) & (cdTS < tearStEnd[1])]

    tearCents = sampSpl[cdTS,:]
    splDer = sampSpl[:-1,:]- sampSpl[1:,:]

    if inLineMax is None:
        inLineMax = np.random.uniform(dirMin,dirMax,size=(1,1))
    if perpMax is None:
        perpMax = np.random.uniform(dirMin,dirMax,size=(1,1))
    
    splDer = np.concatenate((splDer[[0],:],splDer))
    tearDer = splDer[cdTS,:]
    areaMax = inLineMax * perpMax
    tearDensity = areaMax/ ((ptRadius**2)*np.pi)

    nTears = tearCents.shape[0]
    
    tearCts = np.random.randint(l1MinCt,l1MaxCt,size=(nTears,1))
    for tNo in range(len(minDensity)): # build up the layer matrix
        tearCts = np.append(tearCts, np.random.randint(np.ceil(tearDensity*minDensity[tNo]),
                                                       np.ceil(tearDensity*maxDensity[tNo]),size=(nTears,1)),
                            axis = 1)

    tearCtIdxs = np.concatenate((np.zeros((1)),np.cumsum(np.sum(tearCts,axis=1))),axis=0)

    tearXY = np.zeros((np.sum(tearCts),2))
    layerMats = {}
    # generate tears by using random points in layers
    for tIdx in range(len(cdTS)):
        layerMats[tIdx] = {}
        for layer in range(tearCts.shape[1]): # work in layers, each layer builds off of the last, gradually filling out the space
            # each layer builds off the last with a uniform distribution
            nTPts = tearCts[tIdx,layer]
            if layer == 0:
                centPts = np.repeat(np.reshape(tearCents[tIdx,:],(1,2)),nTPts,axis=0)
            else:
                centIdxs = np.random.randint(0,tearCts[tIdx,layer-1],size=(nTPts))
                centPts = layerMats[tIdx][layer-1][centIdxs,:]
            inLineFactor = np.random.uniform(inLinePercs[0,layer]*inLineMax,inLinePercs[1,layer]*inLineMax,size=(nTPts,1))
            perpFactor = np.random.uniform(perpPercs[0,layer]*perpMax,perpPercs[1,layer]*perpMax,size=(nTPts,1))
            cDerIL = tearDer[tIdx,:]
            cDerP = np.array([tearDer[tIdx,1], -tearDer[tIdx,0]])
            totVec = (inLineFactor * cDerIL) + (perpFactor * cDerP)
            newPts = centPts + totVec
            layerMats[tIdx][layer] = newPts.copy()
        idxRng = range(tearCtIdxs[tIdx].astype(int),tearCtIdxs[tIdx+1].astype(int))
        tearXY[idxRng,:] = np.vstack(list(layerMats[tIdx].values()))
    
    # rectify the points so we don't go out of bounds
    tearXY = np.maximum(tearXY,0)
    tearXY[:,0] = np.minimum(tearXY[:,0],dim[0]-1)
    tearXY[:,1] = np.minimum(tearXY[:,1],dim[1]-1)

    # turn these points into a distance mask
    tearMask = np.ones(invDim)
    tearMask[(np.round(tearXY[:,1])).astype(int),np.round(tearXY[:,0]).astype(int)] = 0
    tearDist = morphology.distance_transform_edt(tearMask)
   
    if randEdge == True:
        distRand = np.random.uniform(-int(ptRadius*.5),int(ptRadius*.5),size=invDim)
        tearDist = blur(tearDist+distRand,(5,5))
    tearBW = tearDist <= ptRadius

    alphaArr = (tearBW*tearAlpha*255).astype(np.uint8)
    colorArr = np.zeros((invDim[0],invDim[1],3),dtype=np.uint8)
    edgeArea = np.logical_and(tearDist > ptRadius,tearDist <= ptRadius+edgeWidth)
    
    # determine the color of the edge area and the 
    meanColor = np.mean(np.array(inputIm),axis=(0,1))
    for i in range(len(rgbVal)):
        colorArr[:,:,i] = rgbVal[i]
        colorArr[edgeArea,i] = np.uint8(np.minimum(meanColor[i] * edgeColorMult[i],255))

    alphaArr[edgeArea] = edgeAlpha * 255
    alphaMask = Image.fromarray(alphaArr,'L')
    colorLayer = Image.fromarray(colorArr,'RGB')
    comp_im = Image.composite(colorLayer, inputIm, alphaMask)
    return comp_im
    
def apply_artifact(inputImName,artifactType,outputImName = None, outputDir = None,randAdd = 0, ext = None, perTileRand = None):
    # outputIm = apply_artifact(inputImName,artifactType,outputImName = None, outputDir = None,
    #                           randAdd = 0, ext = None, perTileRand = None):
    #            Commmand line version of this package
    #            Applies the default settings for each of the artifacts for this package
    #            Handles per tile/slide randomization via hashing the file name into a random seed
    #            Leans on the file structure to determine the tile name & slide name
    # 
    # ###
    # Inputs: Required
    #     inputImName: string       The fully qualified name of the input image (include path)
    #        (filename)
    #     artifactType: string      The type of artifact to add
    #                               Currently implemented artifacts:
    #                               'marker', 'fold', 'sectioning', 'illumination', 'bubbles', 'stain', 'tear'
    # Inputs: Optional
    #     outputImName: string      Optional output filename
    #                               currently defaults to original name + '_' + first 4 chars of artifact
    #                               e.g. im1.jpeg -> im1_mark.jpeg
    #     outputDir: string         Optional output directory
    #                               defaults to current directory
    #     randAdd: string           Optional number to add to the random seed, e.g. if additional trials are desired
    #     ext: string               Extension to output the file as (defaults to same as input)
    #       no period               (e.g. 'jpeg', 'png')
    #     perTileRand:              Whether to do randomization by tile or by slide (True = tile, False = slide)
    #        None or True or False  Default (None) = based on the type of artifact
    #                               {'marker' : True, 'fold': True, 'sectioning': True, 'illumination': True, 
    #                                'bubbles': True, 'stain' : False, 'tear': True}
    # ###
    # Output:
    #     outputIm: a PIL Image     A 2D RGB image with artifact added
    # File Output:
    #     Altered image saved to outputImName
    
    
    
    artifactType = artifactType.lower()
    # to remove any linkage between the different types of random addition (e.g. marker vs fold)
    typeSeedAdd = {'marker' : 1, 'fold': 2, 'sectioning': 3, 'illumination': 4, 'bubbles': 5, 'stain' : 6, 'tear': 7}
    # to randomize slide/tile based on type of artifact
    typeTileRand = {'marker' : True, 'fold': True, 'sectioning': True, 'illumination': True, 'bubbles': True, 
                    'stain' : False, 'tear': True}
    
    inputIm = Image.open(inputImName)

    inputImDir,fName = os.path.split(inputImName)
    oPath1, rDir1 = os.path.split(inputImDir)
    _, rDir2 = os.path.split(oPath1)
    fNameNoExt = os.path.splitext(fName)[0]
    if ext is None:
        ext = os.path.splitext(fName)[-1]
    
    if perTileRand is None:
        perTileRand = typeTileRand[artifactType]
    if perTileRand == True: # take into account the tile name
        fID = os.path.join(rDir2,rDir1,fNameNoExt)
    else: # only take into account the slide name
        fID = os.path.join(rDir2,rDir1)

    randMax = (2**32) -1  # max size of the random seed
    # there's potentially some concern about the difference in 32 bit vs 64 bit systems
    h = blake2s()
    h.update(fID.encode('utf-8'))
    h_int = int(h.hexdigest(), 16)

    random_hash = h_int + randAdd + typeSeedAdd[artifactType]
    random_seed = random_hash % randMax
    
    if artifactType == "marker":
        outputIm = add_marker(inputIm,random_seed = random_seed)
    elif artifactType == "fold":
        outputIm = add_fold(inputIm,random_seed = random_seed)
    elif artifactType == "sectioning":
        outputIm = add_sectioning(inputIm,random_seed = random_seed)
    elif artifactType == "illumination":
        outputIm = add_illumination(inputIm,random_seed = random_seed)
    elif artifactType == "bubbles":
        outputIm = add_bubbles(inputIm,random_seed = random_seed)
    elif artifactType == "stain":
        outputIm = add_stain(inputIm,random_seed = random_seed)
    elif artifactType == "tear":
        outputIm = add_tear(inputIm,random_seed = random_seed)
    outputSuffix = artifactType[0:4]
    if outputImName is None:
        outputImName = "%s_%s.%s" % (fNameNoExt, outputSuffix, ext)
        if outputDir is not None:
            if not os.path.exists(outputDir):
                os.makedirs(outputDir)
            outputImName = os.path.join(outputDir,outputImName)
    outputIm.save(outputImName)
    return outputIm

if __name__ == '__main__':
    # Map command line arguments to function arguments.
    apply_artifact(*sys.argv[1:])
