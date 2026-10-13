# Vendored: prove-it-done verifier

* Source: https://github.com/issaquahd/prove-it-done, `scripts/verify_receipts.py`
* Pinned commit: `d897534e817bac6621fae51878270dc24447da14`
* License: MIT (`LICENSE` in this folder), unmodified copy (byte-identical to that commit).
* Why vendored: judges will not have our local checkouts; the receipt ledger in this repo
  is verified by exactly this file (`drift verify-ledger`).
