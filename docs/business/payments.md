# Payments and Subscriptions

This document outlines the financial ecosystem of **OneCreator**, which utilizes a dual-layered model combining subscription-based feature access with a resource-dependent usage credit system (Coins).

## 1. Subscription Plans

The platform offers tiered subscription plans that define the user's access to specific features and tools. While the exact tiers are subject to refinement, the architecture supports:

*   **Feature Gating:** Higher tiers unlock advanced capabilities such as higher resolution exports, additional AI-driven layout strategies, and multi-user workspace collaboration.
*   **Recurring Allocation:** Each subscription plan may include a monthly allowance of base credits (Coins).

## 2. Usage Credit System (OneCreator Coins)

In addition to subscription tiers, OneCreator employs a granular usage-based system utilizing **Coins**. This system is designed to align costs with the actual computational resources consumed during video processing.

### Resource-Based Costing
The cost of processing a video in Coins is calculated based on:
*   **Processing Time:** The duration required by the Render Engine to assemble and export the clip.
*   **Resource Intensity:** Factors such as the use of GPU acceleration, high-resolution AI models (e.g., advanced transcription or face tracking), and the complexity of the applied layout.
*   **Output Length:** The total duration of the final rendered clips.

### Credit Management
Users can purchase Coin top-up packages at any time to ensure uninterrupted service when processing high volumes of content or resource-heavy projects.

## 3. Internal Wallet Architecture (`apps/payments/models.py`)

The financial state is managed at the **Workspace** level, ensuring that credits and plan benefits are shared among authorized team members.

### Core Models

#### Wallet
Each Workspace possesses a central `Wallet` that tracks the current balance of Coins and the active subscription status.
*   **Balance:** A decimal value representing the available Coins.
*   **Plan Status:** Tracks the current tier and expiration of the subscription.

#### Transaction
The system maintains a comprehensive audit trail for every financial movement.
*   **Usage Transactions:** Deductions made for video processing tasks.
*   **Top-up Transactions:** Credits added through package purchases.
*   **Auditing:** Every transaction is linked to the specific user within the workspace who initiated the resource consumption.

## 4. Transaction Lifecycle and Integrity

To prevent over-consumption and ensure data consistency, the system follows a strict reservation protocol:

1.  **Estimation and Reservation:** Before processing begins, the system estimates the required Coin cost and calls `Transaction.reserve_funds()`. This places the Coins in a `PENDING` state.
2.  **Processing:** The Celery workers execute the rendering and AI analysis tasks.
3.  **Finalization:**
    *   **Success:** Upon completion, the exact cost is finalized, and the transaction is marked as `CONFIRMED`.
    *   **Failure:** If a task fails due to a system error, the reserved Coins are automatically returned to the wallet balance via a **Rollback** mechanism.

## 5. External Integration (Stripe)

**Stripe** serves as the primary gateway for all external financial interactions:
*   **Subscription Management:** Handling recurring billing, plan upgrades, and cancellations.
*   **One-Time Top-ups:** Facilitating secure purchases of Coin packages through Stripe Checkout.
*   **Webhooks:** Ensuring real-time synchronization between Stripe payments and the internal Workspace wallets.
