#!/bin/bash
# Debug wrapper script - 确保环境变量正确设置

# 设置环境变量（原机器路径，你机器上没有这些目录可以删掉，不影响）
export GSBAG_SDK=/mnt/data/ubm/xuyanchao/gsbag_x86_Release-4.0.0_20250106_57598d76-Linux
export LD_LIBRARY_PATH=/mnt/data/ubm/xuyanchao/gsbag_x86_Release-4.0.0_20250106_57598d76-Linux/lib:$LD_LIBRARY_PATH
export HOBOT_COM_SDK=/mnt/data/ubm/xuyanchao/gsbag_x86_Release-4.0.0_20250106_57598d76-Linux/external/platform_sdk/
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/root/data/gac-ww-planning/UBM_mining/lib:${HOBOT_COM_SDK}/lib/gacrnd:${HOBOT_COM_SDK}/lib/third_party
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

# vendor 安装的 gsbag 原生库（libgacbag_*.so）在 site-packages 里，必须加进搜索路径
SITE_PKGS=$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2>/dev/null)
[ -n "$SITE_PKGS" ] && export LD_LIBRARY_PATH="$SITE_PKGS:$LD_LIBRARY_PATH"

# 打印环境变量（用于调试）
echo "GSBAG_SDK: $GSBAG_SDK"
echo "LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
echo "HOBOT_COM_SDK: $HOBOT_COM_SDK"
echo "JAVA_HOME: $JAVA_HOME"
echo ""

# 运行 Python 脚本
exec python3 "$@"