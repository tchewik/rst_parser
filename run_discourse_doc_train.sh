export DATA_PATH='./processed_data'
export MODE='train'
export FEAT='char'
export LEARNING_RATE_SCHEDULE='Exponential'
export PRETRAINED_EMBEDDING='data/model.txt'
# export N_EMBED=300

export BATCH_SIZE=2000
# -d 1   - device number
python -m src.cmds.pointing_discourse train -b --buckets 32 --unk 'unknown' -d 1 -p exp/ptb.pointing.discourse.$FEAT \
--data_path $DATA_PATH -f $FEAT --learning_rate_schedule $LEARNING_RATE_SCHEDULE --batch-size $BATCH_SIZE --conf 'discourse_config.ini' \
--embed $PRETRAINED_EMBEDDING
#--n-embed $N_EMBED
