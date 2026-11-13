#!/usr/bin/env bash
#
# chopin (kernel_mt6893) build script.
# Drop into the kernel tree root and run: bash build.sh
# Env: KERNEL_DIR, TOOLCHAIN_DIR, OUT_DIR, USE_SUSFS (0 = syscall table tamper)

# Re-exec under bash when invoked as `sh build.sh`.
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi

# ============================================================
# 基础配置
# ============================================================
KERNEL_DIR="${KERNEL_DIR:-$HOME/kernel/chopin/kernel_mt6893}"
TOOLCHAIN_DIR="${TOOLCHAIN_DIR:-$HOME/toolchains}"
OUT_DIR="${OUT_DIR:-$KERNEL_DIR/out}"
DEFCONFIG_NAME="chopin_defconfig"

die() {
    echo "::error::$*"
    exit 1
}

[ -d "$KERNEL_DIR" ] || die "找不到内核目录: $KERNEL_DIR"
cd "$KERNEL_DIR" || die "无法进入内核目录: $KERNEL_DIR"

# ============================================================
# 辅助函数：实时运行命令
#   run_cmd <check 0|1> <log file or ""> <command...>
# ============================================================
run_cmd() {
    local check="$1"
    local log="$2"
    shift 2
    local rc

    echo "==> 执行: $*"
    if [ -n "$log" ]; then
        "$@" 2>&1 | tee "$log"
        rc=${PIPESTATUS[0]}
    else
        "$@" 2>&1
        rc=$?
    fi

    if [ "$check" -eq 1 ] && [ "$rc" -ne 0 ]; then
        echo "::error::命令执行失败，返回码: $rc"
        exit 1
    fi
    return "$rc"
}

# ============================================================
# 1. 环境变量
# ============================================================
echo "==> 配置环境变量..."
export PATH="$TOOLCHAIN_DIR/aosp-clang/bin:$TOOLCHAIN_DIR/gcc-aarch64-4.9/bin:$TOOLCHAIN_DIR/gcc-arm-4.9/bin:$PATH"
export ARCH=arm64
export CLANG_TRIPLE=aarch64-linux-gnu-
export CROSS_COMPILE=aarch64-linux-android-
export CROSS_COMPILE_ARM32=arm-linux-androideabi-
export CROSS_COMPILE_COMPAT=arm-linux-androideabi-
export LD=ld.lld

# ============================================================
# 2. 彻底恢复并清理源码树
# ============================================================
echo "==> 恢复并清理源码树..."
run_cmd 0 "" git checkout -- . ':(exclude)build.py' ':(exclude)build.sh'

for path in .config .config.old include/generated arch/arm64/include/generated; do
    if [ -e "$path" ]; then
        echo "  -> 删除 $path"
        rm -rf "$path"
    fi
done

# ============================================================
# 3. 集成 SukiSU-Ultra (使用官方脚本 + 修复历史)
# ============================================================
echo "==> 集成 SukiSU-Ultra (官方脚本)..."
rm -rf KernelSU

if [ -L drivers/kernelsu ] || [ -f drivers/kernelsu ]; then
    rm -f drivers/kernelsu
elif [ -d drivers/kernelsu ]; then
    rm -rf drivers/kernelsu
fi

# 运行官方 setup.sh
SETUP_URL="https://raw.githubusercontent.com/SukiSU-Ultra/SukiSU-Ultra/main/kernel/setup.sh"
echo "==> 执行: curl -LSs \"$SETUP_URL\" | bash -s builtin-new"
{ curl -LSs "$SETUP_URL" | bash -s builtin-new; } 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "::error::命令执行失败，返回码: $rc"
    exit 1
fi

# 核心修复：官方脚本是浅克隆，我们在这里补全 Git 历史
if [ -e KernelSU/.git/shallow ]; then
    echo "==> 补全 SukiSU-Ultra 的 Git 历史以获取正确版本号..."
    run_cmd 0 "" git -C KernelSU fetch --unshallow
fi

# 核心修复：官方 Makefile 找的是 main 分支，我们建一个指向当前的 main
run_cmd 0 "" git -C KernelSU branch main

# ============================================================
# 4. 集成 Droidspaces 补丁
# ============================================================
echo "==> 集成 Droidspaces..."
PATCH_SRC="/tmp/droidspaces-src"
rm -rf "$PATCH_SRC"

run_cmd 1 "" git clone --depth=1 --filter=blob:none --sparse \
    https://github.com/seriaTvT/Droidspaces-OSS "$PATCH_SRC"
run_cmd 1 "" git -C "$PATCH_SRC" sparse-checkout set \
    Documentation/resources/kernel-patches/non-GKI

NON_GKI="$PATCH_SRC/Documentation/resources/kernel-patches/non-GKI"
if [ -d "$NON_GKI" ]; then
    for patch_path in "$NON_GKI"/*.patch; do
        [ -e "$patch_path" ] || continue
        patch_file=$(basename "$patch_path")

        if git apply --reverse --check "$patch_path" >/dev/null 2>&1; then
            echo "  ✅ 已应用: $patch_file"
            continue
        fi
        if git apply --check "$patch_path" >/dev/null 2>&1; then
            if git apply "$patch_path"; then
                echo "  ✅ 应用成功: $patch_file"
            else
                echo "  ⚠️ 应用失败: $patch_file"
            fi
        else
            echo "  ⚠️ 跳过冲突补丁: $patch_file"
        fi
    done
fi

# ============================================================
# 5. 修改 defconfig
# ============================================================
echo "==> 修改 $DEFCONFIG_NAME..."
DEFCONFIG_PATH="arch/arm64/configs/$DEFCONFIG_NAME"
[ -f "$DEFCONFIG_PATH" ] || die "未找到: $DEFCONFIG_PATH"

# 确保末尾有换行（与原 Python 行为一致）
[ -n "$(tail -c 1 "$DEFCONFIG_PATH")" ] && echo >> "$DEFCONFIG_PATH"

# 移除旧的后端选择（只清基础 key，子选项留给 olddefconfig 处理）
sed -i -E \
    -e 's/^CONFIG_KSU[= ].*$//' \
    -e 's/^CONFIG_KPM[= ].*$//' \
    -e 's/^CONFIG_KSU_SUSFS[= ].*$//' \
    -e 's/^CONFIG_KSU_TAMPER_SYSCALL_TABLE[= ].*$//' \
    "$DEFCONFIG_PATH"

# 手动选择：KernelSU 后端（USE_SUSFS=0 切回 syscall-table tamper，二者互斥）
if [ -z "${USE_SUSFS+x}" ]; then
    if [ -t 0 ]; then
        echo ""
        echo "请选择 KernelSU 后端:"
        echo "  1) SUSFS inline hooks（默认）"
        echo "  2) syscall table tamper（不带 SUSFS，二者互斥）"
        while :; do
            printf "请输入 1 或 2 [1]: "
            read -r choice || choice=""
            case "$choice" in
                ""|1|2) break ;;
                *) echo "无效输入，请输入 1 或 2。" ;;
            esac
        done
        if [ "$choice" = "2" ]; then USE_SUSFS=0; else USE_SUSFS=1; fi
    else
        USE_SUSFS=1
    fi
else
    if [ "$USE_SUSFS" = "0" ]; then USE_SUSFS=0; else USE_SUSFS=1; fi
fi

if [ "$USE_SUSFS" -eq 1 ]; then
    echo "==> KSU backend: SUSFS inline hooks"
    cat >> "$DEFCONFIG_PATH" <<'EOF'
CONFIG_KSU=y
CONFIG_KPM=y
CONFIG_KSU_SUSFS=y
CONFIG_KSU_SUSFS_INLINE_HOOK=y
CONFIG_KSU_SUSFS_SUS_PATH=y
CONFIG_KSU_SUSFS_SUS_MOUNT=y
CONFIG_KSU_SUSFS_SUS_KSTAT=y
CONFIG_KSU_SUSFS_SPOOF_UNAME=y
CONFIG_KSU_SUSFS_ENABLE_LOG=y
CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y
CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y
CONFIG_KSU_SUSFS_OPEN_REDIRECT=y
CONFIG_KSU_SUSFS_SUS_MAP=y
CONFIG_KSU_SUSFS_PRIORITY_UID_FILTER=y
EOF
else
    echo "==> KSU backend: syscall table tamper"
    cat >> "$DEFCONFIG_PATH" <<'EOF'
CONFIG_KSU=y
CONFIG_KPM=y
CONFIG_KSU_TAMPER_SYSCALL_TABLE=y
EOF
fi

if ! grep -q "Droidspaces support (non-GKI)" "$DEFCONFIG_PATH"; then
    cat >> "$DEFCONFIG_PATH" <<'EOF'

# ===== Droidspaces support (non-GKI) =====
CONFIG_FHANDLE=y
CONFIG_SYSCTL=y
CONFIG_SYSVIPC=y
CONFIG_POSIX_MQUEUE=y
CONFIG_NAMESPACES=y
CONFIG_PID_NS=y
CONFIG_UTS_NS=y
CONFIG_IPC_NS=y
CONFIG_USER_NS=y
CONFIG_SECCOMP=y
CONFIG_SECCOMP_FILTER=y
CONFIG_CGROUPS=y
CONFIG_CGROUP_DEVICE=y
CONFIG_CGROUP_PIDS=y
CONFIG_MEMCG=y
CONFIG_CGROUP_SCHED=y
CONFIG_FAIR_GROUP_SCHED=y
CONFIG_CGROUP_FREEZER=y
CONFIG_CGROUP_NET_PRIO=y
CONFIG_DEVTMPFS=y
CONFIG_OVERLAY_FS=y
CONFIG_TMPFS_POSIX_ACL=y
CONFIG_TMPFS_XATTR=y
CONFIG_FW_LOADER=y
CONFIG_FW_LOADER_USER_HELPER=y
CONFIG_FW_LOADER_COMPRESS=y
CONFIG_NET_NS=y
CONFIG_VETH=y
CONFIG_BRIDGE=y
CONFIG_NETFILTER=y
CONFIG_BRIDGE_NETFILTER=y
CONFIG_NETFILTER_ADVANCED=y
CONFIG_NF_CONNTRACK=y
CONFIG_IP_NF_IPTABLES=y
CONFIG_IP_NF_FILTER=y
CONFIG_NF_NAT=y
CONFIG_NF_TABLES=y
CONFIG_IP_NF_TARGET_MASQUERADE=y
CONFIG_NETFILTER_XT_TARGET_MASQUERADE=y
CONFIG_NETFILTER_XT_TARGET_TCPMSS=y
CONFIG_NETFILTER_XT_MATCH_ADDRTYPE=y
CONFIG_NF_CONNTRACK_NETLINK=y
CONFIG_NF_NAT_REDIRECT=y
CONFIG_IP_ADVANCED_ROUTER=y
CONFIG_IP_MULTIPLE_TABLES=y
CONFIG_NF_CONNTRACK_IPV4=y
CONFIG_NF_NAT_IPV4=y
CONFIG_IP_NF_NAT=y
# CONFIG_ANDROID_PARANOID_NETWORK is not set
CONFIG_NETFILTER_XT_MATCH_COMMENT=y
CONFIG_NETFILTER_XT_MATCH_STATE=y
CONFIG_NETFILTER_XT_MATCH_CONNTRACK=y
CONFIG_NETFILTER_XT_MATCH_MULTIPORT=y
CONFIG_NETFILTER_XT_MATCH_HL=y
CONFIG_NETFILTER_XT_TARGET_REJECT=y
CONFIG_IP_NF_TARGET_REJECT=y
CONFIG_NETFILTER_XT_TARGET_LOG=y
CONFIG_IP_NF_TARGET_ULOG=y
CONFIG_NETFILTER_XT_MATCH_RECENT=y
CONFIG_NETFILTER_XT_MATCH_LIMIT=y
CONFIG_NETFILTER_XT_MATCH_HASHLIMIT=y
CONFIG_NETFILTER_XT_MATCH_OWNER=y
CONFIG_NETFILTER_XT_MATCH_PKTTYPE=y
CONFIG_NETFILTER_XT_MATCH_MARK=y
CONFIG_NETFILTER_XT_TARGET_MARK=y
CONFIG_IP_SET=y
CONFIG_IP_SET_HASH_IP=y
CONFIG_IP_SET_HASH_NET=y
CONFIG_NETFILTER_XT_SET=y
CONFIG_NETFILTER_NETLINK_QUEUE=y
CONFIG_NETFILTER_NETLINK_LOG=y
CONFIG_NETFILTER_XT_TARGET_NFLOG=y
EOF
fi

# ============================================================
# 6. 移除 -dirty 后缀
# ============================================================
echo "==> 移除 -dirty 后缀..."
if [ -f scripts/setlocalversion ]; then
    sed -i 's/-dirty//g' scripts/setlocalversion
fi

# ============================================================
# 7. 清理输出并准备编译
# ============================================================
echo "==> 清理旧输出目录: $OUT_DIR"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

LOG_FILE="$OUT_DIR/build.log"
MAKE_ARGS=(
    make -j32 "O=$OUT_DIR" ARCH=arm64 "CC=ccache clang"
    CLANG_TRIPLE=aarch64-linux-gnu-
    CROSS_COMPILE=aarch64-linux-android-
    CROSS_COMPILE_ARM32=arm-linux-androideabi-
    CROSS_COMPILE_COMPAT=arm-linux-androideabi-
    LD=ld.lld
    "GITHUB_API_BASE="   # 禁用 API 请求，强制使用本地计算
)

# ============================================================
# 8. 编译
# ============================================================
echo "==> 生成 defconfig"
run_cmd 1 "" "${MAKE_ARGS[@]}" "$DEFCONFIG_NAME"
sleep 1  # WSL 缓冲

echo "==> 开始编译 Image dtbs (日志: $LOG_FILE)"
run_cmd 1 "$LOG_FILE" "${MAKE_ARGS[@]}" Image dtbs

# ============================================================
# 9. 产物合并
# ============================================================
echo "==> 校验产物..."
BOOT_DIR="$OUT_DIR/arch/arm64/boot"
IMAGE_PATH="$BOOT_DIR/Image"
DTB_DIR="$BOOT_DIR/dts"
DTB_PATH="$BOOT_DIR/dtb"

[ -f "$IMAGE_PATH" ] || die "Image 缺失"

: > "$DTB_PATH"
if [ -d "$DTB_DIR" ]; then
    while IFS= read -r -d '' dtb; do
        cat "$dtb" >> "$DTB_PATH"
    done < <(find "$DTB_DIR" -type f -name '*.dtb' -print0)
fi

[ -s "$DTB_PATH" ] || die "dtb 为空"

echo "============================================================"
echo "🎉 构建完成！"
echo "  Image: $IMAGE_PATH"
echo "  dtb:   $DTB_PATH"
echo "============================================================"
