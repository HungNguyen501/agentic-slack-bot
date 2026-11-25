class BotsConfigs:
    """The bot registry's query."""

    # Secrets live in Supabase Vault (see V7 migration); inner joins mean a bot whose secret id
    # no longer resolves is treated as not found rather than half-configured.
    SELECT_BOT = """
        SELECT b.id, b.enabled_skills, b.admin_users, b.app_id,
               token.decrypted_secret AS bot_token,
               signing.decrypted_secret AS signing_secret
        FROM bots b
        JOIN vault.decrypted_secrets token ON token.id = b.bot_token_secret_id
        JOIN vault.decrypted_secrets signing ON signing.id = b.signing_secret_secret_id
    """
