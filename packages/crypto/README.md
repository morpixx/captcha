# captcha-crypto

Low-level crypto primitives that keep showing up in antibot sensor payloads: AES-CBC with PKCS7, AES-GCM with AAD, RSA-OAEP/PKCS1v15 public encrypt, rotating-key XOR (Kasada, BDA), HMAC-SHA1/256, raw deflate without the zlib header.

Nothing here is a protocol. These are the bricks each solver glues together in its own way.

## API

```python
from captcha_crypto import (
    aes_cbc_encrypt, aes_cbc_decrypt,
    aes_gcm_encrypt, aes_gcm_decrypt,
    rsa_public_encrypt_oaep, rsa_public_encrypt_pkcs1v15,
    xor_cipher,
    hmac_sha256, hmac_sha1,
    raw_deflate, raw_inflate,
)
```

Everything is bytes-in / bytes-out. Keys are raw bytes. Nonces/IVs are caller-supplied; helpers never generate them silently.

## Why pycryptodome AND cryptography

`cryptography` is the default — it's audited and the primitives are hardened. `pycryptodome` is kept for one reason: a few antibot schemes use AES in CTR-with-custom-counter or weird block-counter modes that `cryptography`'s high-level AEAD interface won't let you build. Those live in dedicated helpers; everything else stays on `cryptography`.
