"""Workflow for making predictions from TCGA slide images using pretrained DeepPATH models
"""

from pathlib import Path

input_dir = Path("work/input/")
intermeiate_dir = Path("work/intermediate/")
output_dir = Path("work/output/")
src_path = Path("DeepPATH/DeepPATH_code/").absolute()
ckpt = "run2a_3D_classifier/"


datasets = [ds.stem for ds in input_dir.iterdir() if ds.is_dir()]
print(datasets)
rule all:
    input: expand(str(output_dir / "{dataset}"),dataset=datasets)


rule tile_images:
    input: directory(str(input_dir / "{dataset}"))
    output: directory(str(intermeiate_dir / "{dataset}" / "tiles/"))
    shell: 
        """
        python '{src_path}/00_preprocessing/0b_tileLoop_deepzoom4.py'  -s 512 -e 0 -j 32 -B 50 -M 20 -o {output} "{input}/*/*svs"  
        """
def find_metadata(wc):
    metadatas = [str(p.absolute()) for p in (input_dir / wc.dataset).glob("metadata.*.json")]
    return metadatas
rule combine_jpeg_dir:
    input: 
        tiles=rules.tile_images.output,
        metadata= find_metadata
    output: directory(str(intermeiate_dir / "{dataset}" / "combine_jpg/"))
    shell:
        """
        mkdir -p {output}
        cd {output}
        echo pwd
        python '{src_path}/00_preprocessing/0d_SortTiles.py' --SourceFolder='../tiles' --Magnification=20.0  --MagDiffAllowed=0 --SortingOption=3  --PatientID=12 --nSplit 0 --JsonFile='{input.metadata}' --PercentTest=100 --PercentValid=0
        """
rule make_tf_record:
    input: rules.combine_jpeg_dir.output
    output: directory(str(intermeiate_dir / "{dataset}" / "tf_records/"))
    shell:
        """
        mkdir -p {output}
        python '{src_path}/00_preprocessing/TFRecord_2or3_Classes/build_TF_test.py' --directory='{input}'  --output_directory='{output}' --num_threads=1 --one_FT_per_Tile=False --ImageSet_basename='test'
        """
rule predict:
    input: 
        data=rules.make_tf_record.output,
    output: directory(str(output_dir / "{dataset}"))
    shell:
        """
        mkdir -p {output}
        python '{src_path}/02_testing/xClasses/nc_imagenet_eval.py' --checkpoint_dir=checkpoints/{ckpt} --eval_dir={output} --data_dir={input.data}  --batch_size 300  --run_once --ImageSet_basename='test_' --ClassNumber 3 --mode='0_softmax'  --TVmode='test'
        """