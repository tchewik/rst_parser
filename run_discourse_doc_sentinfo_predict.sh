export BATCH_SIZE=4000
export TEST_FILE='./processed_data/test_approach1'
export PREDICT_PATH='exp/ptb.pointing.discourse.sent_info.char/2021_12_21_11_25_24/model_dev_UF_26.45_NF_17.44_RF_12.84.pt'
export PREDICT_OUTPUT_PATH='dummy_format_data/pred_file_sent_info'
export FEAT='char'

python -m src.cmds.pointing_discourse_sentinfo predict -b -d 1 -p exp/ptb.pointing.discourse.sent_info.$FEAT.predict \
--data $TEST_FILE --path $PREDICT_PATH --batch-size $BATCH_SIZE --predict_output_path $PREDICT_OUTPUT_PATH