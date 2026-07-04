"""Unit tests for InPost authentication flow module."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.inpost_paczkomaty.exceptions import InPostApiError
from custom_components.inpost_paczkomaty.inpost_auth_flow import InpostAuth
from custom_components.inpost_paczkomaty.models import HttpResponse


# =============================================================================
# InpostAuth Tests
# =============================================================================


class TestInpostAuth:
    """Tests for InpostAuth class."""

    def test_init_default_language(self):
        """Test initialization with default language."""
        auth = InpostAuth()

        assert auth._language == "pl"
        assert "pl-PL" in auth._http_client.headers["Accept-Language"]

    def test_init_english_language(self):
        """Test initialization with English language."""
        auth = InpostAuth(language="en")

        assert auth._language == "en"
        assert "en-US" in auth._http_client.headers["Accept-Language"]

    def test_generate_random_hex(self):
        """Test random hex generation."""
        result = InpostAuth._generate_random_hex(8)

        assert len(result) == 16  # 8 bytes = 16 hex chars
        assert all(c in "0123456789abcdef" for c in result)

    def test_generate_random_hex_different_each_time(self):
        """Test random hex generates different values."""
        result1 = InpostAuth._generate_random_hex(8)
        result2 = InpostAuth._generate_random_hex(8)

        assert result1 != result2

    def test_generate_code_verifier(self):
        """Test code verifier generation."""
        result = InpostAuth._generate_code_verifier()

        # Should only contain alphanumeric characters
        assert result.isalnum()
        assert len(result) > 0

    def test_generate_code_challenge(self):
        """Test code challenge generation from verifier."""
        auth = InpostAuth()

        challenge = auth._generate_code_challenge()

        # Challenge should be base64 URL-safe without padding
        assert "=" not in challenge
        assert len(challenge) > 0

    def test_build_oauth_params(self):
        """Test OAuth parameter building."""
        auth = InpostAuth()

        params = auth._build_oauth_params()

        assert params["response_type"] == "code"
        assert params["client_id"] == "inpost-mobile"
        assert params["redirect_uri"] == "https://account.inpost-group.com/callback"
        assert params["scope"] == "openid"
        assert params["code_challenge_method"] == "S256"
        assert "code_challenge" in params
        assert "state" in params
        assert "nonce" in params

    def test_build_login_url(self):
        """Test building the browser login URL."""
        auth = InpostAuth()

        url = auth.build_login_url()

        assert url.startswith("https://account.inpost-group.com/oauth2/authorize?")
        assert "client_id=inpost-mobile" in url
        assert "code_challenge=" in url
        assert f"state={auth._flow_state}" in url

    # -------------------------------------------------------------------------
    # Authorization code extraction
    # -------------------------------------------------------------------------

    def test_extract_authorization_code_from_full_url(self):
        """Extract the code from a full callback URL and validate state."""
        auth = InpostAuth()
        url = (
            "https://account.inpost-group.com/callback"
            f"?code=auth_code_123&state={auth._flow_state}"
        )

        assert auth.extract_authorization_code(url) == "auth_code_123"

    def test_extract_authorization_code_from_query_string(self):
        """Extract the code from a bare query string (no state to validate)."""
        auth = InpostAuth()

        assert (
            auth.extract_authorization_code("code=auth_code_123")
            == "auth_code_123"
        )

    def test_extract_authorization_code_raw_code(self):
        """A raw code without query syntax is returned as-is."""
        auth = InpostAuth()

        assert auth.extract_authorization_code("raw_code_value") == "raw_code_value"

    def test_extract_authorization_code_state_mismatch(self):
        """A mismatching state raises ValueError."""
        auth = InpostAuth()
        url = "https://example.com/callback?code=abc&state=not_matching"

        with pytest.raises(ValueError, match="State mismatch"):
            auth.extract_authorization_code(url)

    def test_extract_authorization_code_empty(self):
        """Empty input raises ValueError."""
        auth = InpostAuth()

        with pytest.raises(ValueError, match="No authorization code"):
            auth.extract_authorization_code("   ")

    def test_extract_authorization_code_missing_code(self):
        """A URL with a query but no code value raises ValueError."""
        auth = InpostAuth()

        with pytest.raises(ValueError, match="not found"):
            auth.extract_authorization_code("https://example.com/callback?code=")

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_success(self):
        """Test exchanging authorization code for tokens."""
        auth = InpostAuth()

        with patch.object(
            auth._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.return_value = HttpResponse(
                body={
                    "access_token": "access_123",
                    "refresh_token": "refresh_456",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "openid",
                    "id_token": "id_789",
                },
                status=200,
            )

            tokens = await auth.exchange_code_for_tokens("auth_code")

            assert tokens.access_token == "access_123"
            assert tokens.refresh_token == "refresh_456"
            assert tokens.token_type == "Bearer"
            assert tokens.expires_in == 3600
            assert tokens.id_token == "id_789"

        await auth.close()

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_failure(self):
        """Test token exchange failure."""
        auth = InpostAuth()

        with patch.object(
            auth._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.return_value = HttpResponse(
                body={"error": "invalid_grant"},
                status=400,
            )

            with pytest.raises((InPostApiError, ValueError)):
                await auth.exchange_code_for_tokens("invalid_code")

        await auth.close()

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_missing_access_token(self):
        """Test token exchange with missing access_token in response."""
        auth = InpostAuth()

        with patch.object(
            auth._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            # Response is successful but doesn't contain access_token
            mock_post.return_value = HttpResponse(
                body={"refresh_token": "refresh_456"},
                status=200,
            )

            with pytest.raises(ValueError, match="Token exchange failed"):
                await auth.exchange_code_for_tokens("auth_code")

        await auth.close()

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_non_dict_response(self):
        """Test token exchange with non-dict response body."""
        auth = InpostAuth()

        with patch.object(
            auth._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            # Response is successful but body is not a dict
            mock_post.return_value = HttpResponse(
                body="Invalid response",
                status=200,
            )

            with pytest.raises(ValueError, match="Token exchange failed"):
                await auth.exchange_code_for_tokens("auth_code")

        await auth.close()

    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing the auth handler."""
        auth = InpostAuth()

        with patch.object(
            auth._http_client, "close", new_callable=AsyncMock
        ) as mock_close:
            await auth.close()
            mock_close.assert_called_once()


# =============================================================================
# Integration Tests
# =============================================================================


class TestAuthFlowIntegration:
    """Integration tests for the external-browser auth flow."""

    @pytest.mark.asyncio
    async def test_redirect_code_auth_flow(self):
        """Test the full redirect-URL -> code -> tokens flow (mocked)."""
        auth = InpostAuth()

        # The user opens the login URL, logs in and pastes back the callback URL.
        login_url = auth.build_login_url()
        assert "oauth2/authorize" in login_url

        redirect_url = (
            "https://account.inpost-group.com/callback"
            f"?code=auth_code_123&state={auth._flow_state}"
        )
        code = auth.extract_authorization_code(redirect_url)
        assert code == "auth_code_123"

        with patch.object(
            auth._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.return_value = HttpResponse(
                body={
                    "access_token": "access_token_value",
                    "refresh_token": "refresh_token_value",
                },
                status=200,
            )
            tokens = await auth.exchange_code_for_tokens(code)
            assert tokens.access_token == "access_token_value"
            assert tokens.refresh_token == "refresh_token_value"

        await auth.close()
