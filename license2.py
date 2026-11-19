
# app/core/license.py

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import platform
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from pathlib import Path

#BASE_DIR = Path(os.getenv("BASE_DIR", "/app"))
# Docker 中默认为 /app；
# 直接运行 Python 时默认为项目根目录。
BASE_DIR = Path(
    os.getenv(
        "BASE_DIR",
        str(Path(__file__).resolve().parents[2]),
    )
).resolve()
LICENSE_DIR = BASE_DIR / "license"

logger = logging.getLogger(__name__)

INSTALLATION_KEY_PATH = LICENSE_DIR / "installation.key"
LICENSE_FILE_PATH = LICENSE_DIR / "license.lic"
PUBLIC_KEY_PATH = LICENSE_DIR / "public_key.pem"

LICENSE_VERSION = 1

# 过滤 Linux/Windows 常见的无效或通用序列号
_INVALID_VALUES = {
    "",
    "none",
    "unknown",
    "default string",
    "system serial number",
    "to be filled by o.e.m.",
    "to be filled by oem",
    "not specified",
    "not applicable",
    "n/a",
    "na",
    "0",
    "00000000",
    "0000000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "00000000-0000-0000-0000-000000000000",
}


def _normalize_value(value):
    """清洗硬件标识，过滤无效值。"""
    if value is None:
        return None

    value = str(value).strip().strip("\x00").strip()
    if not value:
        return None

    normalized = value.casefold()
    if normalized in _INVALID_VALUES:
        return None

    if set(normalized.replace("-", "")) <= {"0", "f"}:
        return None

    return value


def _read_text(path):
    try:
        return Path(path).read_text(
            encoding="utf-8",
            errors="ignore",
        ).strip()
    except (OSError, PermissionError):
        return None


def _get_linux_hardware():
    """
    Linux 硬件信息。
    Docker 部署优先读取挂载进来的 /host_dmi。
    直接 Python 部署读取 /sys/class/dmi/id。
    """
    dmi_dirs = [
        Path("/host_dmi"),
        Path("/sys/class/dmi/id"),
    ]

    fields = {
        "system_uuid": "product_uuid",
        "board_serial": "board_serial",
        "system_serial": "product_serial",
        "bios_serial": "product_serial",
    }

    result = {}

    for name, filename in fields.items():
        for dmi_dir in dmi_dirs:
            value = _normalize_value(
                _read_text(dmi_dir / filename)
            )
            if value:
                result[name] = value
                break

    # product_serial 与 system_serial 来源相同，不重复参与指纹
    if "system_serial" in result:
        result.pop("bios_serial", None)

    return result


def _get_windows_hardware():
    """
    Windows 使用 PowerShell + CIM 获取硬件信息。
    不依赖 wmic，也不需要安装 pywin32。
    """
    script = r"""
$sys = Get-CimInstance Win32_ComputerSystemProduct
$board = Get-CimInstance Win32_BaseBoard
$bios = Get-CimInstance Win32_BIOS

$result = @{
    system_uuid = $sys.UUID
    board_serial = $board.SerialNumber
    system_serial = $sys.IdentifyingNumber
    bios_serial = $bios.SerialNumber
}

$result | ConvertTo-Json -Compress
"""

    commands = [
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        ["pwsh", "-NoProfile", "-NonInteractive",
         "-Command", script],
    ]

    output = None

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
                encoding="utf-8",
                errors="replace",
            )
            output = completed.stdout.strip()
            if output:
                break
        except (OSError, subprocess.SubprocessError):
            continue

    if not output:
        raise RuntimeError(
            "无法读取 Windows 硬件信息，请确认 PowerShell 和 CIM 服务可用。"
        )

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Windows 硬件信息解析失败。"
        ) from exc

    return {
        key: value
        for key, raw_value in data.items()
        if (value := _normalize_value(raw_value))
    }


def get_hardware_fingerprint():
    """
    获取当前机器的硬件指纹。
    不使用 IP 地址和 MAC 地址。
    """
    system = platform.system().lower()

    if system == "linux":
        hardware = _get_linux_hardware()
    elif system == "windows":
        hardware = _get_windows_hardware()
    else:
        raise RuntimeError(
            f"暂不支持当前操作系统：{platform.system()}"
        )

    if not hardware:
        raise RuntimeError(
            "未能读取有效的主板/整机硬件标识。"
            "请检查系统权限、BIOS/DMI 信息或 Docker DMI 挂载。"
        )

    # 将操作系统类型也纳入指纹，避免跨平台身份混用
    return {
        "platform": system,
        "hardware": dict(sorted(hardware.items())),
    }


def _get_installation_secret():
    """
    首次运行生成 32 字节随机安装身份。
    通过 O_EXCL 防止并发首次启动时互相覆盖。
    """
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(
            str(INSTALLATION_KEY_PATH),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        pass
    else:
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(secrets.token_bytes(32))
                file.flush()
                os.fsync(file.fileno())
        except Exception:
            try:
                INSTALLATION_KEY_PATH.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    try:
        secret = INSTALLATION_KEY_PATH.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"无法读取安装身份文件：{INSTALLATION_KEY_PATH}"
        ) from exc

    if len(secret) != 32:
        raise RuntimeError(
            "installation.key 文件长度异常。"
            "请勿手动修改该文件；如文件损坏，需要重新生成机器码并重新授权。"
        )

    # Windows 上 chmod 不等于完整 ACL 保护，仍需限制目录访问权限
    try:
        os.chmod(INSTALLATION_KEY_PATH, 0o600)
    except OSError:
        pass

    return secret


def generate_machine_code():
    """
    机器码 = SHA256(硬件指纹 + 独立安装身份)。
    """
    hardware = get_hardware_fingerprint()
    installation_secret = _get_installation_secret()

    canonical_hardware = json.dumps(
        hardware,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    material = (
        b"auto-video-mark-license-v1\x00"
        + canonical_hardware
        + b"\x00"
        + installation_secret
    )

    return hashlib.sha256(material).hexdigest()


def get_gpu_uuids():
    """
    获取 NVIDIA GPU UUID。
    未安装 NVIDIA 驱动或没有 NVIDIA GPU 时返回空列表。
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=uuid",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    return sorted({
        value.strip()
        for value in result.stdout.splitlines()
        if value.strip()
    })


def _canonical_json(data):
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _parse_expiry(value):
    if value is None:
        return None

    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError("授权有效期格式错误。") from exc

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def verify_license():
    """
    验证授权文件。
    返回 (是否成功, 消息, payload)。
    """
    if not PUBLIC_KEY_PATH.is_file():
        return False, (
            f"缺少客户端公钥文件：{PUBLIC_KEY_PATH}"
        ), None

    if not LICENSE_FILE_PATH.is_file():
        return False, (
            f"缺少授权文件：{LICENSE_FILE_PATH}"
        ), None

    try:
        public_key = serialization.load_pem_public_key(
            PUBLIC_KEY_PATH.read_bytes()
        )

        if not isinstance(public_key, rsa.RSAPublicKey):
            raise RuntimeError("授权公钥不是 RSA 公钥。")

        envelope = json.loads(
            LICENSE_FILE_PATH.read_text(
                encoding="utf-8"
            )
        )

        payload = envelope["payload"]
        signature = base64.b64decode(
            envelope["signature"],
            validate=True,
        )

        if payload.get("version") != LICENSE_VERSION:
            raise RuntimeError("授权文件版本不支持。")

        # 验证 RSA-PSS 数字签名
        public_key.verify(
            signature,
            _canonical_json(payload),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        current_machine_code = generate_machine_code()

        if not hmac.compare_digest(
            str(payload.get("machine_code", "")),
            current_machine_code,
        ):
            raise RuntimeError(
                "机器码不匹配：该授权文件不属于当前设备。"
            )

        expires_at = _parse_expiry(
            payload.get("expires_at")
        )

        if expires_at and datetime.now(timezone.utc) > expires_at:
            raise RuntimeError(
                f"授权已过期：{expires_at.isoformat()}"
            )

        # 只有授权 payload 明确写入 gpu_uuid 才进行 GPU 检查
        licensed_gpu = payload.get("gpu_uuid")

        if licensed_gpu:
            available_gpus = get_gpu_uuids()

            if not any(
                hmac.compare_digest(
                    str(licensed_gpu).casefold(),
                    gpu.casefold(),
                )
                for gpu in available_gpus
            ):
                raise RuntimeError(
                    "GPU UUID 与授权不匹配，或当前设备无法读取 NVIDIA GPU UUID。"
                )

        return True, "授权验证通过。", payload

    except Exception as exc:
        logger.exception("授权验证失败")
        return False, str(exc), None


def license_auth():
    """
    保持现有 main.py 使用的返回结构：
    result_auth['code'] 和 result_auth['result']['message']。
    """
    try:
        ok, message, payload = verify_license()

        if not ok:
            return {
                "code": 0,
                "result": {"message": message},
            }

        return {
            "code": 1,
            "result": {
                "message": message,
                "license_id": payload.get("license_id"),
                "customer": payload.get("customer"),
                "features": payload.get("features", []),
                "expires_at": payload.get("expires_at"),
            },
        }

    except Exception as exc:
        logger.exception("授权模块异常")
        return {
            "code": 0,
            "result": {"message": str(exc)},
        }


def main():
    parser = argparse.ArgumentParser(
        description="Auto Video Mark 授权工具"
    )
    parser.add_argument(
        "--machine-code",
        action="store_true",
        help="生成并显示当前设备机器码",
    )
    parser.add_argument(
        "--gpu-uuids",
        action="store_true",
        help="显示当前 NVIDIA GPU UUID",
    )

    args = parser.parse_args()

    if args.machine_code:
        print(generate_machine_code())
        return

    if args.gpu_uuids:
        values = get_gpu_uuids()
        print("\n".join(values) if values else "未检测到 NVIDIA GPU")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
