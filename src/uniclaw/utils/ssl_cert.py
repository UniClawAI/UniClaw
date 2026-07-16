"""SSL 证书工具:自动生成自签名证书。"""

from __future__ import annotations

import os
from pathlib import Path


def get_or_create_certs(domain: str = "") -> tuple[str, str]:
    """获取或创建 SSL 证书。

    证书保存在 ~/.UniClaw/ssl/ 目录下。
    支持多种域名格式:
    - example.com.key.pem / example.com.cert.pem
    - sub.example.com.key.pem / sub.example.com.cert.pem
    - _.example.com.key.pem / _.example.com.cert.pem(通配符)
    - key.pem / cert.pem(默认)

    Args:
        domain: 可选的域名,如 "uniclaw.example.com"

    Returns:
        (keyfile_path, certfile_path) 元组
    """
    from uniclaw.context import get_app_dir

    cert_dir = get_app_dir() / "ssl"
    cert_dir.mkdir(parents=True, exist_ok=True)

    # 尝试查找已有的证书文件
    if domain:
        keyfile, certfile = _find_existing_certs(cert_dir, domain)
        if keyfile and certfile:
            return str(keyfile), str(certfile)

    # 使用默认文件名
    keyfile = cert_dir / "key.pem"
    certfile = cert_dir / "cert.pem"

    # 如果证书已存在且未过期,直接返回
    if keyfile.exists() and certfile.exists():
        if not _is_cert_expired(certfile):
            return str(keyfile), str(certfile)

    # 生成新证书
    _generate_self_signed_cert(keyfile, certfile, domain)
    return str(keyfile), str(certfile)


def _find_existing_certs(cert_dir: Path, domain: str) -> tuple[Path | None, Path | None]:
    """查找已有的证书文件,支持多种命名格式。

    查找顺序:
    1. {domain}.key.pem / {domain}.cert.pem
    2. {domain_without_wildcard}.key.pem / {domain_without_wildcard}.cert.pem
    3. _.{domain}.key.pem / _.{domain}.cert.pem
    4. 匹配域名后缀的证书(如 sub.example.com 匹配 example.com 的证书)
    """
    # 1. 精确匹配
    keyfile = cert_dir / f"{domain}.key.pem"
    certfile = cert_dir / f"{domain}.cert.pem"
    if keyfile.exists() and certfile.exists() and not _is_cert_expired(certfile):
        return keyfile, certfile

    # 2. 去掉通配符前缀匹配(如 *.example.com -> example.com)
    clean_domain = domain.lstrip("*.")
    if clean_domain != domain:
        keyfile = cert_dir / f"{clean_domain}.key.pem"
        certfile = cert_dir / f"{clean_domain}.cert.pem"
        if keyfile.exists() and certfile.exists() and not _is_cert_expired(certfile):
            return keyfile, certfile

    # 3. 通配符格式匹配(_.example.com)
    keyfile = cert_dir / f"_.{clean_domain}.key.pem"
    certfile = cert_dir / f"_.{clean_domain}.cert.pem"
    if keyfile.exists() and certfile.exists() and not _is_cert_expired(certfile):
        return keyfile, certfile

    # 4. 后缀匹配(查找包含域名后缀的证书)
    for key_path in cert_dir.glob("*.key.pem"):
        cert_path = key_path.with_suffix("").with_suffix(".cert.pem")
        if not cert_path.exists():
            continue
        if _is_cert_expired(cert_path):
            continue
        # 检查证书是否包含该域名
        cert_name = key_path.stem.replace(".key", "")
        if domain.endswith(cert_name) or cert_name.endswith(clean_domain):
            return key_path, cert_path

    return None, None


def _is_cert_expired(certfile: Path) -> bool:
    """检查证书是否已过期。"""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        cert_data = certfile.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
        from datetime import datetime
        return cert.not_valid_after_utc < datetime.now(cert.not_valid_after_utc.tzinfo)
    except Exception:
        return True


def _generate_self_signed_cert(keyfile: Path, certfile: Path, domain: str = "") -> None:
    """生成自签名证书。"""
    print(f"[SSL] 生成自签名证书: {certfile}")
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        from datetime import datetime, timedelta, timezone
        import ipaddress

        # 生成私钥
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )

        # 获取本机 IP
        local_ip = _get_local_ip()

        # 构建证书主题
        common_name = domain if domain else "UniClaw"
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        # 构建 SAN 列表
        san_list = [
            x509.DNSName("localhost"),
            x509.DNSName("*.local"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]

        # 添加域名
        if domain:
            san_list.append(x509.DNSName(domain))

        # 添加局域网 IP
        if local_ip:
            try:
                san_list.append(x509.IPAddress(ipaddress.IPv4Address(local_ip)))
            except Exception:
                pass

        # 构建证书
        now = datetime.now(timezone.utc)
        cert_builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName(san_list),
                critical=False,
            )
        )

        # 签名
        cert = cert_builder.sign(key, hashes.SHA256(), default_backend())

        # 保存私钥
        keyfile.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        # 保存证书
        certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    except ImportError:
        # 如果没有 cryptography 库,使用 openssl 命令
        _generate_with_openssl(keyfile, certfile, domain)


def _generate_with_openssl(keyfile: Path, certfile: Path, domain: str = "") -> None:
    """使用 openssl 命令生成证书(备用方案)。"""
    import subprocess
    import tempfile

    local_ip = _get_local_ip()
    san_list = ["DNS:localhost", "DNS:*.local", "IP:127.0.0.1"]

    # 添加域名
    if domain:
        san_list.append(f"DNS:{domain}")

    # 添加局域网 IP
    if local_ip:
        san_list.append(f"IP:{local_ip}")

    san = ",".join(san_list)
    common_name = domain if domain else "UniClaw"

    # 创建临时配置文件
    config_content = f"""
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = {common_name}

[v3_req]
subjectAltName = {san}
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.cnf', delete=False) as f:
        f.write(config_content)
        config_file = f.name

    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(keyfile),
            "-out", str(certfile),
            "-days", "365",
            "-nodes",
            "-config", config_file,
        ], check=True, capture_output=True)
    finally:
        os.unlink(config_file)


def _get_local_ip() -> str:
    """获取本机局域网 IP 地址。"""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return ""
