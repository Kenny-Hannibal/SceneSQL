# 默认请求超时时间（秒）
DEFAULT_TIMEOUT_SECONDS = 1500

# 环境名称常量
ENV_DEV = "dev"
ENV_UAT = "uat"
ENV_PROD = "prod"

# 默认环境
DEFAULT_ENV = ENV_PROD

# 服务名称常量
SERVICE_RAW_DATA = "raw_data"
SERVICE_PROD_DATA = "prod_data"
SERVICE_DATASET = "dataset"
SERVICE_CERBERUS = "cerberus"

# ALB 基地址，本地调试时通过 host 参数直接指定地址，不使用 env
_ALB_BASE = {
    "dev": "http://172.31.75.131:10300/api",
    "uat": "http://alb-pw4syww5v1fa3vkfdx.cn-wulanchabu.alb.aliyuncsslb.com/api",
    "prod": "http://infra.gacrnd.com/api",
}

# 服务在 ALB 上的路径后缀
_SERVICE_PATHS = {
    SERVICE_RAW_DATA: "dataAccess",
    SERVICE_PROD_DATA: "dataPipeline",
    SERVICE_DATASET: "dataset",
    SERVICE_CERBERUS: "cerberus",
}

_SERVICE_HOSTS = {}
for _env in _ALB_BASE:
    _SERVICE_HOSTS[_env] = {
        svc: f"{_ALB_BASE[_env]}/{path}"
        for svc, path in _SERVICE_PATHS.items()
    }

# Dataset OSS Bucket 名称配置
_DATASET_OSS_BUCKET_NAME = {
    "dev": "cn-ad-collection-issue",
    "uat": "cn-ad-collection-issue",
    "prod": "gacrnd-ali-dataset",
}

_KAFKA_TRACK_TOPIC = "topic_dm_sdk_tracking"

# Kafka Bootstrap Servers 配置
_KAFKA_BOOTSTRAP_SERVERS = {
    "dev": [
        "alikafka-post-cn-usr4m50cl001-1-vpc.alikafka.aliyuncs.com:9092",
        "alikafka-post-cn-usr4m50cl001-2-vpc.alikafka.aliyuncs.com:9092",
        "alikafka-post-cn-usr4m50cl001-3-vpc.alikafka.aliyuncs.com:9092",
    ],
    "uat": [
        "alikafka-post-cn-cpa4lq4n5003-1-vpc.alikafka.aliyuncs.com:9092",
        "alikafka-post-cn-cpa4lq4n5003-2-vpc.alikafka.aliyuncs.com:9092",
        "alikafka-post-cn-cpa4lq4n5003-3-vpc.alikafka.aliyuncs.com:9092",
    ],
    "prod": [
        "alikafka-pre-cn-axc49vm3t002-1-vpc.alikafka.aliyuncs.com:9092",
        "alikafka-pre-cn-axc49vm3t002-2-vpc.alikafka.aliyuncs.com:9092",
        "alikafka-pre-cn-axc49vm3t002-3-vpc.alikafka.aliyuncs.com:9092",
    ],
}

_LOCAL_CERBERUS = "http://172.31.75.131:10300/api/cerberus"


def get_host(env: str, service_name: str):
    """
    获取服务 host 地址。

    :param env: 环境 (dev/uat/prod)，必填。
    :param service_name: 服务名称 (raw_data, prod_data, dataset, cerberus)，必填。
    :return: 服务地址
    :raises ValueError: 当 env 不是 dev/uat/prod, 或 service_name 不合法时抛出
    """
    if env not in _SERVICE_HOSTS:
        raise ValueError("env must be one of dev, uat, prod")
    if service_name not in _SERVICE_HOSTS[env]:
        raise ValueError(
            f"service_name must be one of {', '.join(_SERVICE_HOSTS[env].keys())}"
        )
    return _SERVICE_HOSTS[env][service_name]


def get_cerberus_host(env: str) -> str:
    """获取 cerberus 服务地址。

    :param env: 环境 (dev/uat/prod)，必填。
    :return: cerberus 服务地址
    :raises ValueError: 当 env 不是 dev/uat/prod 时抛出
    """
    if env not in _SERVICE_HOSTS:
        raise ValueError("env must be one of dev, uat, prod")
    return _SERVICE_HOSTS[env].get("cerberus", _LOCAL_CERBERUS)


def get_dataset_oss_bucket_name(env: str) -> str:
    """
    获取 Dataset OSS Bucket 名称。

    :param env: 环境 (dev, uat, prod)，必填。
    :return: OSS Bucket 名称
    :raises ValueError: 当 env 不是 dev/uat/prod 时抛出
    """
    if env not in _DATASET_OSS_BUCKET_NAME:
        raise ValueError("env must be one of dev, uat, prod")
    return _DATASET_OSS_BUCKET_NAME[env]


def get_kafka_bootstrap_servers(env: str) -> list:
    """
    获取 Kafka Bootstrap Servers。

    :param env: 环境 (dev, uat, prod)，必填。
    :return: Kafka Bootstrap Servers 列表
    :raises ValueError: 当 env 不是 dev/uat/prod 时抛出
    """
    if env not in _KAFKA_BOOTSTRAP_SERVERS:
        raise ValueError("env must be one of dev, uat, prod")
    return _KAFKA_BOOTSTRAP_SERVERS[env]


def get_kafka_track_topic() -> str:
    return _KAFKA_TRACK_TOPIC
