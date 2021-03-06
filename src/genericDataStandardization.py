import sys
import time
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from utils.data_asset import DataAsset
from utils.comUtils import *
from utils.standardizationUtils import *
from utils.athena_ddl import get_or_create_db, get_or_create_table, manage_partition
from connector import Connector


def get_global_config():
    """
    Utility method to get global config file
    :return:JSON
    """
    config_file_path = "globalConfig.json"
    file = open(file=config_file_path, mode="r")
    config = json.load(file)
    file.close()
    return config


# Get the arguments
args = getResolvedOptions(sys.argv, ["source_path", "source_id", "asset_id", "exec_id", "JOB_NAME"])
global_config = get_global_config()

# Record the start time of the job
start_time = time.time()

# Creating a spark session object
spark = sql.SparkSession.builder.getOrCreate()
# Creating a job
job = Job(GlueContext(SparkContext.getOrCreate()))
job.init(args["JOB_NAME"], args)
db_secret = global_config['db_secret']
db_region = global_config['db_region']
conn = Connector(db_secret, db_region, autocommit=True)
# Create object to store data asset info
asset = DataAsset(args, global_config, run_identifier="data-standardization", conn=conn)
# Update the data catalog dynamoDB table to "In-Progress" for easy monitoring
asset.update_data_catalog(conn, data_standardization="In-Progress",data_standardization_exec_id=args['JOB_RUN_ID'])
path_for_standardization = asset.get_masking_path()
try:
    # Creating spark dataframe from input file.Supported:CSV,Parquet,JSON,ORC
    source_df = create_spark_df(
        spark,
        path_for_standardization,
        asset.asset_file_type,
        asset.asset_file_delim,
        asset.asset_file_header,
        asset.logger,
    )
    # Getting data asset table dedicated for a specific asset which specifies if masking is required or not
    metadata = asset.get_asset_metadata(conn)

    # Function to standardize the data to a user required format
    result = run_data_standardization(source_df, metadata, asset.logger)

    # Getting data from target system table
    target_system_info = get_target_system_info(conn, asset.target_id, asset.logger)

    # Getting the timestamp identifier from the source path
    timestamp = get_timestamp(asset.source_path)

    # Getting the standardization path with the help of info from target system
    target_path = get_standardization_path(
        target_system_info, asset.asset_id, timestamp, asset.logger
    )
    # Writing the standardized data to the target path in parquet format
    result.repartition(1).write.parquet(target_path, mode="overwrite")

    # Update data asset catalog with tha path of target file
    asset.update_data_catalog(conn, tgt_file_path=target_path)

    # Storing the target file to Athena with DB = Domain and Table = Sub-domain_AssetId
    domain = target_system_info["domain"]
    workgroup = global_config['workgroup']
    get_or_create_db(asset.region, domain, workgroup, asset.logger)
    athena_path = get_athena_path(target_system_info, asset.asset_id)

    # Updating the data catalog table to "Completed" if the standardization is successful
    asset.update_data_catalog(conn, data_standardization="Completed")

    # Create table in Athena
    get_or_create_table(
        asset.region,
        result,
        target_system_info,
        asset.asset_name,
        athena_path,
        wg=workgroup,
        partition=True,
        encrypt=asset.encryption,
        logger=asset.logger,
    )

    # Add partitions for the table
    manage_partition(
        asset.region,
        target_system_info,
        asset.asset_name,
        timestamp,
        target_path,
        wg=workgroup,
        logger=asset.logger,
    )

except Exception as e:
    asset.logger.write(message=str(e))
    # Updating the data catalog table to "Failed" in case of exceptions
    asset.update_data_catalog(conn, data_standardization="Failed")
    asset.logger.write_logs_to_s3()
    raise sys.exit()

end_time = time.time()
total_time_taken = float("{0:.2f}".format(end_time - start_time))
asset.logger.write(message=f"Time Taken = {total_time_taken} seconds")

# Write logs to S3
asset.logger.write_logs_to_s3()

# Close connection
conn.close()

# Stop the spark session
stop_spark(spark)

# Committing the job
job.commit()
