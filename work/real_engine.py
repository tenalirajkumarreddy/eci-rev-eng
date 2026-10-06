#!/usr/bin/env python3
"""Real engine harness"""
import hashlib
import base64
APP_CONST = "IPS2062445"
def get_hash_new(input_str, secure_key):
    s = (str(input_str).strip() + str(secure_key).strip()).encode("utf-8")
    return hashlib.sha512(s).hexdigest()
