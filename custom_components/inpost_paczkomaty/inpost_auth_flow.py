"""
InPost Authentication Module for Home Assistant.

This module handles the OAuth2 authentication flow for InPost services.
"""

import base64
import binascii
import hashlib
import logging
import os
import re
from urllib.parse import parse_qs, urlencode

from .const import (
    API_BASE_URL,
    OAUTH_BASE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
)
from .http_client import HttpClient
from .models import AuthTokens
from .utils import get_language_code

_LOGGER = logging.getLogger(__name__)


class InpostAuth:
    """
    InPost OAuth2 Authentication Handler.

    The user performs the interactive login (phone number, SMS code, captcha and
    email confirmation) in an external browser window. The authenticated browser
    session cookie is then injected here so we can mint an authorization code
    server-side (using our own PKCE) and exchange it for access/refresh tokens.
    """

    # Use constants from const.py
    OAUTH_BASE_URL = OAUTH_BASE_URL
    API_BASE_URL = API_BASE_URL
    CLIENT_ID = OAUTH_CLIENT_ID
    REDIRECT_URI = OAUTH_REDIRECT_URI

    def __init__(self, language: str = "pl") -> None:
        """Initialize the InPost authentication handler."""
        self._language = language
        self._language_code = get_language_code(language)
        self._http_client = HttpClient(
            custom_headers={"Accept-Language": self._language_code}
        )
        self._flow_state = self._generate_random_hex(8)
        self._code_verifier = self._generate_code_verifier()
        _LOGGER.debug("InpostAuth initialized with flow state: %s", self._flow_state)

    @staticmethod
    def _generate_random_hex(length: int) -> str:
        """
        Generate a random hexadecimal string.

        Args:
            length: Number of random bytes to generate.

        Returns:
            Hexadecimal string representation.
        """
        return binascii.hexlify(os.urandom(length)).decode("utf-8")

    @staticmethod
    def _generate_code_verifier() -> str:
        """
        Generate a PKCE code verifier.

        Returns:
            URL-safe code verifier string.
        """
        verifier = base64.urlsafe_b64encode(os.urandom(39)).decode("utf-8")
        # Remove non-alphanumeric characters for URL safety
        return re.sub(r"[^a-zA-Z0-9]+", "", verifier)

    def _generate_code_challenge(self) -> str:
        """
        Generate a PKCE code challenge from the code verifier.

        Returns:
            Base64 URL-safe encoded SHA256 hash of the code verifier.
        """
        digest = hashlib.sha256(self._code_verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8")
        # Remove padding characters per PKCE spec
        return challenge.replace("=", "")

    def _build_oauth_params(self) -> dict:
        """
        Build OAuth2 authorization request parameters.

        Returns:
            Dictionary of OAuth2 parameters.
        """
        return {
            "response_type": "code",
            "client_id": self.CLIENT_ID,
            "redirect_uri": self.REDIRECT_URI,
            "scope": "openid",
            "code_challenge": self._generate_code_challenge(),
            "code_challenge_method": "S256",
            "theme": "light",
            "state": self._flow_state,
            "nonce": self._generate_random_hex(8),
            "lang": self._language,
            "response_mode": "query",
        }

    def build_login_url(self) -> str:
        """
        Build the InPost login URL for the user to open in a browser.

        Opening this URL triggers InPost's own login flow (phone number, SMS
        code, captcha and email confirmation). After a successful login the
        browser holds an authenticated ``SESSION`` cookie that can be pasted
        back into Home Assistant.

        Returns:
            Fully-qualified OAuth2 authorize URL.
        """
        return f"{self.OAUTH_BASE_URL}/oauth2/authorize?{urlencode(self._build_oauth_params())}"

    def extract_authorization_code(self, redirect_input: str) -> str:
        """
        Extract the OAuth2 authorization code from the user's browser redirect.

        After the user logs in via ``build_login_url()``, InPost redirects the
        browser to ``.../callback?code=...&state=...``. The user pastes back
        either that full URL or just the ``code`` value.

        Args:
            redirect_input: The pasted callback URL, a bare query string, or the
                raw authorization code.

        Returns:
            The OAuth2 authorization code.

        Raises:
            ValueError: If no code can be extracted or the state does not match.
        """
        value = (redirect_input or "").strip()
        if not value:
            raise ValueError("No authorization code provided")

        # A pasted URL / query string contains "code=...".
        if "code=" in value:
            query = value.split("?", 1)[1] if "?" in value else value
            params = parse_qs(query)

            codes = params.get("code")
            if not codes or not codes[0]:
                raise ValueError("Authorization code not found in redirect URL")

            state = params.get("state", [None])[0]
            if state and state != self._flow_state:
                raise ValueError("State mismatch in redirect URL")

            _LOGGER.debug("Authorization code extracted from redirect URL")
            return codes[0]

        # Otherwise treat the whole input as the raw authorization code.
        _LOGGER.debug("Authorization code provided directly")
        return value

    async def exchange_code_for_tokens(self, authorization_code: str) -> AuthTokens:
        """
        Step 7: Exchange authorization code for access and refresh tokens.

        Args:
            authorization_code: The OAuth2 authorization code.

        Returns:
            AuthTokens dataclass with token data.

        Raises:
            InPostApiError: If token exchange fails with API error.
            ValueError: If token exchange fails for other reasons.
        """
        _LOGGER.info("Exchanging authorization code for tokens")
        url = f"{self.API_BASE_URL}/global/oauth2/token"
        response = await self._http_client.post(
            url=url,
            data={
                "client_id": self.CLIENT_ID,
                "code": authorization_code,
                "code_verifier": self._code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": self.REDIRECT_URI,
            },
        )

        # Check for API errors
        response.raise_for_error()

        if not isinstance(response.body, dict) or "access_token" not in response.body:
            _LOGGER.error("Token exchange failed: %s", response.body)
            raise ValueError(f"Token exchange failed: {response.body}")

        _LOGGER.info("Tokens obtained successfully")
        return AuthTokens(
            access_token=response.body["access_token"],
            refresh_token=response.body["refresh_token"],
            token_type=response.body.get("token_type", "Bearer"),
            expires_in=response.body.get("expires_in", 7199),
            scope=response.body.get("scope", "openid"),
            id_token=response.body.get("id_token"),
        )

    async def close(self) -> None:
        """Close the HTTP client session."""
        await self._http_client.close()
        _LOGGER.debug("InpostAuth session closed")
