#!/bin/bash
#SBATCH --account=rrg-bengioy-ad
#SBATCH --cpus-per-task=32
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --time=48:00:00
#SBATCH -x blg4101
#SBATCH -o /home/codevilf/scratch/logs/slurm-%j.out

pkill -9 python

# Load your environment
module load python/3.7
module load cuda/10.1

source ~/scratch/dif/bin/activate

# chose the experiment config file
#CONFIG=configs/v2/downstream/from_image/detection/voc_fpn.yaml
#CONFIG=configs/v2/downstream/from_format/cl assification/r-101_imagenet.yaml
#CONFIG=configs/v2/downstream/from_image/classification/r-101_imagenet.yaml
#CONFIG=configs/v2/format/1_decoders/image/hific_mse_plain.yaml
#CONFIG=configs/v2/downstream/from_image/detection/voc_fpn.yaml
#CONFIG=configs/v2/downstream/from_format/classification/r-101_detectron2.yaml
#CONFIG=configs/v2/downstream/from_format/sem_seg/r101_DLv3+_voc_bpp0100.yaml
#CONFIG=configs/v2/downstream/from_format/detection/voc_bpp1_v3.yaml
cd $HOME/CompressAI/
#cd $HOME/differentiabledata_test/
export DD_PATH=$SLURM_TMPDIR/datasets
mkdir $DD_PATH
export VIMEO_RAW=$DD_PATH/vimeo_triplet.tar.gz
export VIMEO=$DD_PATH/vimeo
mkdir $VIMEO
rsync -r --info=progress2 /home/codevilf/scratch/vimeo_triplet/vimeo_triplet.tar.gz $DD_PATH
time tar -xzf $VIMEO_RAW --strip=2 --directory $VIMEO
mkdir $VIMEO/test
mkdir $VIMEO/train
#mv files.txt test/
mv $VIMEO/images/* $VIMEO/test/
mv $VIMEO/0000*.png $VIMEO/train/
mv $VIMEO/0001*.png $VIMEO/train/
mv $VIMEO/0002*.png $VIMEO/train/
mv $VIMEO/0003*.png $VIMEO/train/
mv $VIMEO/0004*.png $VIMEO/train/
mv $VIMEO/*png $VIMEO/train/

wandb init -p dif -e duneboy
echo "login to wandb ..."
wandb login 0e8fd5a4c74280f2811004aecb1f4022aca8c319
echo "login to wandb ... DONE"

exec python3 examples/train_minnen.py -d $VIMEO --epochs 300 -lr 1e-4 --batch-size 16 --cuda --save --experiment minnen

#python3 compute_imagenet_bpp.py --path $IMAGENET
#python3 compute_dataset_bpp.py --path $DD_PATH/VOC2012/JPEGImages
#python3 compute_jpeg_bpp.py --path $DD_PATH/VOC2012/JPEGImages
