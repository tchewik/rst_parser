export BATCH_SIZE=10000
#export TEST_FILE='dummy_format_data/sample_rawtext_data_format'
export TEST_FILE='./processed_data/test_approach1'
export PREDICT_PATH='./exp/ptb.pointing.discourse.char/2021_12_20_14_05_28/model_dev_UF_19.73_NF_11.65_RF_7.83.pt'
export PREDICT_OUTPUT_PATH='./dummy_format_data/prediction_test'
export FEAT='char'
export PRETRAINED_EMBEDDING='./data/model.txt'
export N_EMBED=300

python -m src.cmds.pointing_discourse predict  -b \
  --data $TEST_FILE \
  -p exp/ptb.pointing.discourse.$FEAT.predict \
  --buckets 32 \
  --path $PREDICT_PATH \
  --batch-size $BATCH_SIZE \
  --predict_output_path $PREDICT_OUTPUT_PATH
