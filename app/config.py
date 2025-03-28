#!/usr/bin/env python3

from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    admin_username: str
    admin_password: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    database_url: str
    redis_url: str
    s3_bucket_name: str
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"  # Default to OpenAI's endpoint
    openai_model: str = "gpt-4o-mini"  # Default model
    workdir: str
    dropbox_app_key: str
    dropbox_app_secret: str
    dropbox_folder: str
    dropbox_refresh_token: str
    nextcloud_upload_url: str
    nextcloud_username: str
    nextcloud_password: str
    nextcloud_folder: str
    paperless_ngx_api_token: str
    paperless_host: str
    azure_ai_key: str
    azure_region: str
    azure_endpoint: str
    gotenberg_url: str
    external_hostname: str = "localhost"  # Default to localhost
    
    # Authentik
    authentik_client_id: Optional[str] = None
    authentik_client_secret: Optional[str] = None
    authentik_config_url: Optional[str] = None
    auth_enabled: bool = True  # Default to enabled

    # IMAP 1
    imap1_host: Optional[str] = None
    imap1_port: Optional[int] = 993
    imap1_username: Optional[str] = None
    imap1_password: Optional[str] = None
    imap1_ssl: bool = True
    imap1_poll_interval_minutes: int = 5
    imap1_delete_after_process: bool = False

    # IMAP 2
    imap2_host: Optional[str] = None
    imap2_port: Optional[int] = 993
    imap2_username: Optional[str] = None
    imap2_password: Optional[str] = None
    imap2_ssl: bool = True
    imap2_poll_interval_minutes: int = 10
    imap2_delete_after_process: bool = False

    class Config:
        env_file = ".env"

settings = Settings()
