"""Self-signed TLS certificate for serving OrbitWatch over HTTPS on localhost (SRS 4.2)."""
import ipaddress
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from orbitwatch import config

CERT = config.CERT_DIR / "orbitwatch-localhost.crt"
KEY = config.CERT_DIR / "orbitwatch-localhost.key"


def make_cert():
    """Return (cert, key) paths, creating a certificate valid for localhost / 127.0.0.1 if needed."""
    if CERT.exists() and KEY.exists():
        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        if cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=7):
            return str(CERT), str(KEY)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OrbitWatch (development)")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(CERT), str(KEY)
