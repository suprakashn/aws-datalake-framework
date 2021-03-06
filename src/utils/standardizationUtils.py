import pyspark
from pyspark import sql
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from .logger import log


@log
def run_data_standardization(source_df, metadata, logger=None):
    """
    :param source_df: The spark dataframe
    :param metadata: The metadata
    :param logger:
    :return:
    """
    for i in metadata:
        col_name = i.get("col_nm")
        # Gets the target column name from the metadata
        target_col_name = i.get("tgt_col_nm")
        # Replaces the name of the columns with user required name
        source_df = source_df.withColumnRenamed(col_name, target_col_name)
    logger.write(message="Data standardization done successfully")
    # Returns spark dataframe
    return source_df
