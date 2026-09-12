import secrets

def generate_production_secrets():
    """Generates 256-bit high-entropy secrets for JWT and internal token signing."""
    jwt_secret = secrets.token_hex(32)       # 64 hex chars = 256 bits entropy
    internal_secret = secrets.token_hex(24)  # 48 hex chars = 192 bits entropy
    
    print("=" * 60)
    print("NomadX Cryptographic Production Secret Generator")
    print("=" * 60)
    print(f"Generated JWT_SECRET (256-bit):      {jwt_secret}")
    print(f"Generated INTERNAL_SECRET (192-bit): {internal_secret}")
    print("=" * 60)
    return jwt_secret, internal_secret

if __name__ == "__main__":
    generate_production_secrets()
