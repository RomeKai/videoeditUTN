# Social Media Integrations

This document details the planned architecture for integrating **OneCreator** with external social media platforms for automated content distribution and authentication.

## 1. Planned Integrations

The system is designed to support direct exports and synchronization with the following platforms:

### YouTube
*   **OAuth2 Integration:** Users will be able to connect their YouTube channels securely.
*   **Shorts Automation:** Direct upload functionality for rendered 9:16 clips as YouTube Shorts.
*   **Metadata Management:** Automated generation of titles, descriptions, and tags based on video transcription.

### Twitch
*   **Clip Sourcing:** Integration with the Twitch API to pull existing VODs and clips directly into OneCreator for further optimization.
*   **Authentication:** Social login capabilities using Twitch credentials.

## 2. Implementation Status

Current files in `apps/integrations/` (e.g., `youtube_api.py`, `twitch_api.py`, `social_auth.py`) serve as architectural stubs. These features are prioritized in the upcoming development roadmap.

## 3. Architecture for Social Auth

The proposed architecture follows standard **OAuth2** flows:
1.  **Authorization Request:** The user is redirected to the provider's (Google/Twitch) consent screen.
2.  **Callback Handling:** The backend receives an authorization code and exchanges it for access and refresh tokens.
3.  **Token Management:** Tokens are encrypted and stored at the Workspace level to allow all authorized editors to distribute content to the connected channels.
4.  **Automatic Refresh:** A background task will handle the periodic refreshing of access tokens to ensure uninterrupted service.

## 4. Distribution Workflow

Once fully implemented, the distribution workflow will be as follows:
1.  **Selection:** User selects a completed clip from their dashboard.
2.  **Platform Configuration:** User chooses the destination platform (YouTube/Twitch) and provides final metadata.
3.  **Dispatch:** A Celery task handles the binary transfer and API communication with the external provider.
4.  **Confirmation:** The system records the external URL and status (e.g., "Published", "Draft") within the OneCreator database.
