export DATA_PATH='./processed_data'
export MODE='train'
export FEAT='char'
export LEARNING_RATE_SCHEDULE='Exponential'
export PRETRAINED_EMBEDDING='data/model.txt'
export N_EMBED=300

export BERT_MODEL='xlnet-base-cased'
export BATCH_SIZE=4000
export DEVICE=1
python -m src.cmds.pointing_discourse_sentinfo train -b -d $DEVICE -p exp/ptb.pointing.discourse.sent_info.$FEAT \
--data_path $DATA_PATH -f $FEAT --learning_rate_schedule $LEARNING_RATE_SCHEDULE \
--bert $BERT_MODEL --batch-size $BATCH_SIZE --embed $PRETRAINED_EMBEDDING --n-embed $N_EMBED --unk 'unknown'
#--conf 'discourse_config_bert.ini'
#--embed $PRETRAINED_EMBEDDING
#--n-embed $N_EMBED