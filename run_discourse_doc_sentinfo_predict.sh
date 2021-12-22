export BATCH_SIZE=4000
export TEST_FILE='./processed_data/test_approach1'
export PREDICT_PATH='exp/ptb.pointing.discourse.sent_info.char/2021_12_22_16_04_39/model_dev_UF_20.37_NF_11.38_RF_8.13.pt'
export PREDICT_OUTPUT_PATH='dummy_format_data/pred_file_sent_info'
export FEAT='char'

python -m src.cmds.pointing_discourse_sentinfo predict -b -d 1 -p exp/ptb.pointing.discourse.sent_info.$FEAT.predict \
--data $TEST_FILE --path $PREDICT_PATH --batch-size $BATCH_SIZE --predict_output_path $PREDICT_OUTPUT_PATH