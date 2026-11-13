#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import re
import time

# ============================================================
# 基础配置
# ============================================================
KERNEL_DIR = os.environ.get("KERNEL_DIR", os.path.expanduser("~/kernel/chopin/kernel_mt6893"))
TOOLCHAIN_DIR = os.environ.get("TOOLCHAIN_DIR", os.path.expanduser("~/toolchains"))
OUT_DIR = os.environ.get("OUT_DIR", os.path.join(KERNEL_DIR, "out"))
DEFCONFIG_NAME = "chopin_defconfig"

if not os.path.isdir(KERNEL_DIR):
    print(f"::error::找不到内核目录: {KERNEL_DIR}")
    sys.exit(1)
os.chdir(KERNEL_DIR)

# ============================================================
# 辅助函数：实时运行命令
# ============================================================
def run_cmd(cmd, log_file=None, env=None, check=True, shell=False):
    print(f"==> 执行: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    try:
        with subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env, shell=shell
        ) as p:
            if log_file:
                f_log = open(log_file, "w", encoding="utf-8")
            for line in p.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                if log_file:
                    f_log.write(line)
                    f_log.flush()
            if log_file:
                f_log.close()
            p.wait()
            if check and p.returncode != 0:
                print(f"::error::命令执行失败，返回码: {p.returncode}")
                sys.exit(1)
            return p.returncode
    except KeyboardInterrupt:
        print("\n::warn::用户强制中断")
        sys.exit(1)

# ============================================================
# 1. 环境变量
# ============================================================
print("==> 配置环境变量...")
env = os.environ.copy()
env["PATH"] = f"{TOOLCHAIN_DIR}/aosp-clang/bin:{TOOLCHAIN_DIR}/gcc-aarch64-4.9/bin:{TOOLCHAIN_DIR}/gcc-arm-4.9/bin:" + env.get("PATH", "")
env["ARCH"] = "arm64"
env["CLANG_TRIPLE"] = "aarch64-linux-gnu-"
env["CROSS_COMPILE"] = "aarch64-linux-android-"
env["CROSS_COMPILE_ARM32"] = "arm-linux-androideabi-"
env["CROSS_COMPILE_COMPAT"] = "arm-linux-androideabi-"
env["LD"] = "ld.lld"

# ============================================================
# 2. 彻底恢复并清理源码树
# ============================================================
print("==> 恢复并清理源码树...")
run_cmd(["git", "checkout", "--", ".", ":(exclude)build.py"], env=env, check=False)

paths_to_remove = [
    ".config", 
    ".config.old", 
    "include/generated", 
    "arch/arm64/include/generated"
]
for path in paths_to_remove:
    if os.path.exists(path):
        print(f"  -> 删除 {path}")
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except Exception as e:
            print(f"  ⚠️ 删除 {path} 失败: {e}")

# ============================================================
# 3. 集成 SukiSU-Ultra (使用官方脚本 + 修复历史)
# ============================================================
print("==> 集成 SukiSU-Ultra (官方脚本)...")
if os.path.exists("KernelSU"):
    shutil.rmtree("KernelSU")

if os.path.lexists("drivers/kernelsu"):
    if os.path.islink("drivers/kernelsu") or os.path.isfile("drivers/kernelsu"):
        os.unlink("drivers/kernelsu")
    else:
        shutil.rmtree("drivers/kernelsu")

# 运行官方 setup.sh
setup_cmd = 'curl -LSs "https://raw.githubusercontent.com/SukiSU-Ultra/SukiSU-Ultra/main/kernel/setup.sh" | bash -s builtin-new'
run_cmd(setup_cmd, env=env, shell=True)

# 🚀 核心修复：官方脚本是浅克隆，我们在这里补全 Git 历史
if os.path.exists("KernelSU/.git/shallow"):
    print("==> 补全 SukiSU-Ultra 的 Git 历史以获取正确版本号...")
    run_cmd(["git", "-C", "KernelSU", "fetch", "--unshallow"], env=env, check=False)

# 🚀 核心修复：官方 Makefile 找的是 main 分支，我们建一个指向当前的 main
run_cmd(["git", "-C", "KernelSU", "branch", "main"], env=env, check=False)

# ============================================================
# 4. 集成 Droidspaces 补丁
# ============================================================
print("==> 集成 Droidspaces...")
PATCH_SRC = "/tmp/droidspaces-src"
if os.path.exists(PATCH_SRC):
    shutil.rmtree(PATCH_SRC)

run_cmd(["git", "clone", "--depth=1", "--filter=blob:none", "--sparse", "https://github.com/seriaTvT/Droidspaces-OSS", PATCH_SRC], env=env)
run_cmd(["git", "-C", PATCH_SRC, "sparse-checkout", "set", "Documentation/resources/kernel-patches/non-GKI"], env=env)

NON_GKI = os.path.join(PATCH_SRC, "Documentation/resources/kernel-patches/non-GKI")
if os.path.isdir(NON_GKI):
    for patch_file in os.listdir(NON_GKI):
        if not patch_file.endswith(".patch"): continue
        patch_path = os.path.join(NON_GKI, patch_file)
        ret = subprocess.run(["git", "apply", "--reverse", "--check", patch_path], capture_output=True, text=True)
        if ret.returncode == 0:
            print(f"  ✅ 已应用: {patch_file}"); continue
        ret = subprocess.run(["git", "apply", "--check", patch_path], capture_output=True, text=True)
        if ret.returncode == 0:
            subprocess.run(["git", "apply", patch_path])
            print(f"  ✅ 应用成功: {patch_file}")
        else:
            print(f"  ⚠️ 跳过冲突补丁: {patch_file}")

# ============================================================
# 5. 修改 defconfig
# ============================================================
print(f"==> 修改 {DEFCONFIG_NAME}...")
DEFCONFIG_PATH = f"arch/arm64/configs/{DEFCONFIG_NAME}"
if not os.path.exists(DEFCONFIG_PATH):
    print(f"::error::未找到: {DEFCONFIG_PATH}"); sys.exit(1)

with open(DEFCONFIG_PATH, "r") as f:
    defconfig = f.read()
if not defconfig.endswith("\n"): defconfig += "\n"

for key in ["CONFIG_KSU", "CONFIG_KPM", "CONFIG_KSU_SUSFS", "CONFIG_KSU_TAMPER_SYSCALL_TABLE"]:
    defconfig = re.sub(rf"^{key}[= ].*$", "", defconfig, flags=re.MULTILINE)
# 手动选择：KernelSU 后端（USE_SUSFS=0 切回 syscall-table tamper，二者互斥）
_env_mode = os.environ.get("USE_SUSFS")
if _env_mode is None:
    if sys.stdin.isatty():
        print("")
        print("请选择 KernelSU 后端:")
        print("  1) SUSFS inline hooks（默认）")
        print("  2) syscall table tamper（不带 SUSFS，二者互斥）")
        while True:
            try:
                _choice = input("请输入 1 或 2 [1]: ").strip()
            except EOFError:
                _choice = ""
            if _choice in ("", "1", "2"):
                break
            print("无效输入，请输入 1 或 2。")
        USE_SUSFS = (_choice != "2")
    else:
        USE_SUSFS = True
else:
    USE_SUSFS = _env_mode != "0"
if USE_SUSFS:
    KSU_OPTS = [
        "CONFIG_KSU=y",
        "CONFIG_KPM=y",
        "CONFIG_KSU_SUSFS=y",
        "CONFIG_KSU_SUSFS_INLINE_HOOK=y",
        "CONFIG_KSU_SUSFS_SUS_PATH=y",
        "CONFIG_KSU_SUSFS_SUS_MOUNT=y",
        "CONFIG_KSU_SUSFS_SUS_KSTAT=y",
        "CONFIG_KSU_SUSFS_SPOOF_UNAME=y",
        "CONFIG_KSU_SUSFS_ENABLE_LOG=y",
        "CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y",
        "CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y",
        "CONFIG_KSU_SUSFS_OPEN_REDIRECT=y",
        "CONFIG_KSU_SUSFS_SUS_MAP=y",
        "CONFIG_KSU_SUSFS_PRIORITY_UID_FILTER=y",
    ]
else:
    KSU_OPTS = [
        "CONFIG_KSU=y",
        "CONFIG_KPM=y",
        "CONFIG_KSU_TAMPER_SYSCALL_TABLE=y",
    ]
print("==> KSU backend: " + ("SUSFS inline hooks" if USE_SUSFS else "syscall table tamper"))
defconfig += "\n".join(KSU_OPTS) + "\n"

if "Droidspaces support (non-GKI)" not in defconfig:
    defconfig += """
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
"""

with open(DEFCONFIG_PATH, "w") as f:
    f.write(defconfig)

# ============================================================
# 6. 移除 -dirty 后缀
# ============================================================
print("==> 移除 -dirty 后缀...")
setlocalversion = "scripts/setlocalversion"
if os.path.exists(setlocalversion):
    with open(setlocalversion, "r") as f:
        content = f.read()
    with open(setlocalversion, "w") as f:
        f.write(content.replace("-dirty", ""))

# ============================================================
# 7. 清理输出并准备编译
# ============================================================
print(f"==> 清理旧输出目录: {OUT_DIR}")
if os.path.exists(OUT_DIR):
    try: shutil.rmtree(OUT_DIR)
    except: subprocess.run(["rm", "-rf", OUT_DIR])
os.makedirs(OUT_DIR, exist_ok=True)

LOG_FILE = os.path.join(OUT_DIR, "build.log")
MAKE_ARGS = [
    "make", "-j32", f"O={OUT_DIR}", "ARCH=arm64", "CC=ccache clang",
    "CLANG_TRIPLE=aarch64-linux-gnu-",
    "CROSS_COMPILE=aarch64-linux-android-",
    "CROSS_COMPILE_ARM32=arm-linux-androideabi-",
    "CROSS_COMPILE_COMPAT=arm-linux-androideabi-",
    "LD=ld.lld",
    "GITHUB_API_BASE=" # 禁用 API 请求，强制使用本地计算
]

# ============================================================
# 8. 编译
# ============================================================
print("==> 生成 defconfig")
run_cmd(MAKE_ARGS + [DEFCONFIG_NAME], env=env)
time.sleep(1)  # WSL 缓冲

print(f"==> 开始编译 Image dtbs (日志: {LOG_FILE})")
run_cmd(MAKE_ARGS + ["Image", "dtbs"], log_file=LOG_FILE, env=env)

# ============================================================
# 9. 产物合并
# ============================================================
print("==> 校验产物...")
BOOT_DIR = os.path.join(OUT_DIR, "arch/arm64/boot")
IMAGE_PATH = os.path.join(BOOT_DIR, "Image")
DTB_DIR = os.path.join(OUT_DIR, "arch/arm64/boot/dts")
DTB_PATH = os.path.join(BOOT_DIR, "dtb")

if not os.path.exists(IMAGE_PATH):
    print("::error::Image 缺失"); sys.exit(1)

with open(DTB_PATH, "wb") as outfile:
    for root, dirs, files in os.walk(DTB_DIR):
        for file in files:
            if file.endswith(".dtb"):
                with open(os.path.join(root, file), "rb") as infile:
                    outfile.write(infile.read())

if os.path.getsize(DTB_PATH) == 0:
    print("::error::dtb 为空"); sys.exit(1)

print("============================================================")
print("🎉 构建完成！")
print(f"  Image: {IMAGE_PATH}")
print(f"  dtb:   {DTB_PATH}")
print("============================================================")
