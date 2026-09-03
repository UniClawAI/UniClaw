"""utils/ssl_cert.py 域名匹配逻辑测试。

只测纯逻辑部分 _find_existing_certs / _is_cert_expired,
不测真实的证书生成(需要 cryptography 密钥生成,慢且属 IO)。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from uniclaw.utils.ssl_cert import _find_existing_certs, _is_cert_expired


def _make_cert_files(
    cert_dir: Path,
    name: str,
    *,
    expired: bool = False,
) -> tuple[Path, Path]:
    """在 cert_dir 下生成 {name}.key.pem / {name}.cert.pem 测试证书对。"""
    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    now = datetime.now(timezone.utc)
    not_after = now - timedelta(days=1) if expired else now + timedelta(days=365)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256(), default_backend())
    )
    keyfile = cert_dir / f"{name}.key.pem"
    certfile = cert_dir / f"{name}.cert.pem"
    keyfile.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return keyfile, certfile


class TestIsCertExpired:
    """_is_cert_expired 过期检测。"""

    def test_valid_cert_not_expired(self, tmp_path):
        _, certfile = _make_cert_files(tmp_path, "example.com")
        assert _is_cert_expired(certfile) is False

    def test_expired_cert(self, tmp_path):
        _, certfile = _make_cert_files(tmp_path, "example.com", expired=True)
        assert _is_cert_expired(certfile) is True

    def test_missing_file_treated_as_expired(self, tmp_path):
        """文件不存在按已过期处理。"""
        assert _is_cert_expired(tmp_path / "nope.pem") is True

    def test_garbage_file_treated_as_expired(self, tmp_path):
        """内容非法按已过期处理,不抛异常。"""
        bad = tmp_path / "bad.pem"
        bad.write_text("not a certificate", encoding="utf-8")
        assert _is_cert_expired(bad) is True


class TestFindExistingCerts:
    """_find_existing_certs 四级域名匹配逻辑。"""

    def test_exact_match(self, tmp_path):
        """1. 精确匹配 {domain}.pem。"""
        _make_cert_files(tmp_path, "sub.example.com")
        key, cert = _find_existing_certs(tmp_path, "sub.example.com")
        assert key == tmp_path / "sub.example.com.key.pem"
        assert cert == tmp_path / "sub.example.com.cert.pem"

    def test_exact_match_expired_falls_through(self, tmp_path):
        """精确匹配到但已过期时继续向下查找。"""
        _make_cert_files(tmp_path, "example.com", expired=True)
        key, cert = _find_existing_certs(tmp_path, "example.com")
        assert key is None and cert is None

    def test_wildcard_domain_matches_clean_cert(self, tmp_path):
        """2. *.example.com 匹配 example.com 的证书。"""
        _make_cert_files(tmp_path, "example.com")
        key, cert = _find_existing_certs(tmp_path, "*.example.com")
        assert key == tmp_path / "example.com.key.pem"

    def test_wildcard_cert_format_match(self, tmp_path):
        """3. example.com 匹配 _.example.com 命名的通配符证书。"""
        _make_cert_files(tmp_path, "_.example.com")
        key, cert = _find_existing_certs(tmp_path, "example.com")
        assert key == tmp_path / "_.example.com.key.pem"

    def test_suffix_match(self, tmp_path):
        """4. sub.example.com 后缀匹配 example.com 的证书。"""
        _make_cert_files(tmp_path, "example.com")
        key, cert = _find_existing_certs(tmp_path, "sub.example.com")
        assert key == tmp_path / "example.com.key.pem"

    def test_suffix_match_requires_dot_boundary(self, tmp_path):
        """后缀匹配必须以点为边界: notexample.com 不匹配 example.com。"""
        _make_cert_files(tmp_path, "notexample.com")
        key, cert = _find_existing_certs(tmp_path, "example.com")
        assert key is None and cert is None

    def test_no_match(self, tmp_path):
        _make_cert_files(tmp_path, "other.org")
        key, cert = _find_existing_certs(tmp_path, "example.com")
        assert key is None and cert is None

    def test_empty_dir_no_match(self, tmp_path):
        key, cert = _find_existing_certs(tmp_path, "example.com")
        assert key is None and cert is None

    def test_suffix_match_skips_expired(self, tmp_path):
        """后缀匹配跳过已过期证书。"""
        _make_cert_files(tmp_path, "example.com", expired=True)
        key, cert = _find_existing_certs(tmp_path, "sub.example.com")
        assert key is None and cert is None

    def test_suffix_match_requires_cert_pair(self, tmp_path):
        """只有 key 没有对应 cert 的孤儿文件不参与匹配。"""
        _make_cert_files(tmp_path, "example.com")
        (tmp_path / "orphan.key.pem").write_bytes(b"junk")
        key, cert = _find_existing_certs(tmp_path, "orphan.example.com")
        # 匹配到的是 example.com 的证书对,而不是孤儿 key
        assert key == tmp_path / "example.com.key.pem"

    def test_exact_match_priority_over_suffix(self, tmp_path):
        """精确匹配优先于后缀匹配。"""
        _make_cert_files(tmp_path, "example.com")
        _make_cert_files(tmp_path, "sub.example.com")
        key, _ = _find_existing_certs(tmp_path, "sub.example.com")
        assert key == tmp_path / "sub.example.com.key.pem"
