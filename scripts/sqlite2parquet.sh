source .venv/bin/activate

REPO_HASH=$(cd /root/data/data_mining/UBM_mining/ubm_data_mining && git rev-parse HEAD)

nohup python -u agent/backend/app/services/etl/etl_sqlite_to_parquet.py \
    --source-dir /mnt/gacrnd-oss/gac_liulian/common_data/sqlite_dbs/20260515_T68_1131_5bb5ec_1.5w \
    --output-dir /mnt/gacrnd-oss/gac_liulian/common_data/parquet/20260515_T68_1131_5bb5ec_1.5w \
    --batch-id 20260515_T68_1131_5bb5ec_1.5w \
    --repo-hash "$REPO_HASH" \
    --tables dynamic_obj static_obj static_link dynamic_lane dynamic_link \
    > etl_retry_v2.log 2>&1 &

  tail -f /root/data/text2sql/etl_retry_v2.log