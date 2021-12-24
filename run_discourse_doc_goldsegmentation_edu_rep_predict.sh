export TEST_FILE='./processed_data/test_approach1'
#export PREDICT_PATH='saved_model/model_pointing_discourse_gold_segmentation_edu_rep_Full_RNF_51.1.pt'
PREDICT_PATH='exp/ptb.pointing.discourse.gold_segmentation_edu_rep.char/2021_12_24_15_17_42/model_dev_UF_63.94_NF_42.44_RF_30.33.pt'
export PREDICT_OUTPUT_PATH='dummy_format_data/predicted_beam'
export BATCH_SIZE=4000
export BEAM_SIZE=20

python -m src.cmds.pointing_discourse_gold_segmentation_edu_rep predict -b -d 0 --data $TEST_FILE \
--path $PREDICT_PATH --batch-size $BATCH_SIZE --beam-size $BEAM_SIZE --predict_output_path $PREDICT_OUTPUT_PATH