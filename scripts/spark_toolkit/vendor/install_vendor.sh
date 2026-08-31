#!/bin/bash
# =============================================================
# 从 vendor 副本安装 gsbag / dm_sdk 到当前 Python 环境。
#
# 背景：这两个内部包的原安装包已丢失，实测可行的装法是把
# 原虚拟环境（text2sql/.venv，Python 3.10）site-packages 里的
# 文件直接复制过来——本目录就是从那个 venv 原样导出的副本。
#
# 用法:
#   bash install_vendor.sh                 # 装到默认 python3
#   bash install_vendor.sh /path/to/.venv/bin/python   # 指定解释器
# =============================================================
set -e
cd "$(dirname "$0")"

PY=${1:-python3}
SP=$($PY -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
echo "目标解释器: $($PY -V 2>&1)"
echo "site-packages: $SP"

PY_MINOR=$($PY -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if [ "$PY_MINOR" != "3.10" ]; then
    echo "警告: gsbag 原生封装(.so)来自 Python 3.10 的 venv，"
    echo "      其他版本导入 gsbag 可能失败（dm_sdk 不受影响，>=3.8 即可）。"
    read -r -p "仍要继续安装吗? [y/N] " ans
    [ "$ans" = "y" ] || [ "$ans" = "Y" ] || exit 1
fi

cp -r gsbag "$SP/"
cp -r gsbag-14.dist-info "$SP/"
cp -P gsbag_reader_wrapper.so gsbag_writer_wrapper.so "$SP/"
cp -r dm_sdk "$SP/"
cp -r dm_sdk-2.7.0.dist-info "$SP/"
cp -P lib*.so* "$SP/"

# dm_sdk 的 pip 依赖预检
MISSING=$($PY -c "
import importlib.util
deps = {'kafka': 'kafka-python', 'cachetools': 'cachetools', 'alibabacloud_oss_v2': 'alibabacloud-oss-v2'}
print(' '.join(pkg for m, pkg in deps.items() if not importlib.util.find_spec(m)))
")
if [ -n "$MISSING" ]; then
    echo ""
    echo "!! dm_sdk 缺少 pip 依赖，请先执行: $PY -m pip install $MISSING"
fi

echo ""
echo "--- 验证 ---"
# 换到临时目录执行，避免被当前目录下的副本遮蔽；
# 原生库（lib*.so*）装在 site-packages，需进 LD_LIBRARY_PATH 才能被 wrapper 找到
cd "$(mktemp -d)"
LD_LIBRARY_PATH="$SP:$LD_LIBRARY_PATH" $PY -c "
import dm_sdk
print('dm_sdk OK')
import gsbag.gsbag_reader
print('gsbag reader OK')
import gsbag.gsbag_writer
print('gsbag writer OK')
"
echo ""
echo "安装完成。注意：导入 gsbag 时需保证 LD_LIBRARY_PATH 包含 $SP"
echo "（spark_toolkit/run_debug.sh 已自动处理）。"
