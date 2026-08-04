#!/bin/bash
# SceneSQL 镜像构建脚本（对齐 DataMining build.sh 风格）
#
# 用法:
#   ./docker-build.sh <tag>           # 仅本地构建镜像 scenesql:<tag>
#   ./docker-build.sh <tag> --push    # 构建并推送到镜像仓库
#
# 环境变量:
#   REGISTRY  镜像仓库前缀，默认 acr-zhijia-registry-vpc.cn-wulanchabu.cr.aliyuncs.com/gacrnd-zhijia/data
#   IMAGE     镜像名，默认 scenesql

set -e

tag=$1
if [ -z "$tag" ]; then
    echo "用法: $0 <tag> [--push]   例: $0 v1.0-api-contract"
    exit 1
fi

REGISTRY=${REGISTRY:-acr-zhijia-registry-vpc.cn-wulanchabu.cr.aliyuncs.com/gacrnd-zhijia/data}
IMAGE=${IMAGE:-scenesql}
full_name="$REGISTRY/$IMAGE:$tag"

echo "开始构建镜像 $full_name"
# --network=host：本机容器默认网络无外网，构建期 npm/pip/apt 需借宿主机网络
docker build --network=host -t "$full_name" .

if [ "$2" = "--push" ]; then
    echo "开始推送镜像 $full_name"
    docker push "$full_name"
    echo "推送完成。DataMining 侧配置 scenesql.image=$full_name 即可切换版本"
else
    echo "构建完成（未推送）。推送: $0 $tag --push"
fi
