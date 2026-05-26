# Product Requirements Document (PRD) - AI Extension

**Title:** Tau2 Bench Retail Agent
**Date:** 2026-05-17
**Solution Category:** AI Agent

---

## Product Purpose & Value Proposition

**Elevator Pitch:**
An AI-powered retail customer service agent that handles order management, returns, exchanges, and account modifications through natural language conversation, following strict policy rules and requiring explicit user confirmation before any state-changing actions.

**Product Objectives (Prioritized):**
1. Authenticate users securely via email or name + zip code
2. Provide order, product, and profile information to authenticated users
3. Cancel or modify pending orders per policy
4. Process returns and exchanges for delivered orders
5. Modify user default addresses
6. Transfer to human agents when requests exceed agent capabilities

---

## Business Context & AI Rationale

**Why AI?:**
The agent automates routine retail customer service tasks (order lookups, cancellations, modifications, returns, exchanges) that follow well-defined policies, reducing the need for human agent involvement while maintaining accuracy and policy compliance.

---

## Goals and Non-Goals

### Goals (In Scope)
- Authenticate users by email or name + zip code before any action
- Look up and display order details, product information, and user profiles
- Cancel pending orders with valid reasons ("no longer needed" or "ordered by mistake")
- Modify pending order shipping address, payment method, or item variants
- Process returns on delivered orders with refund to original payment or gift card
- Exchange items in delivered orders to different variants of the same product
- Modify user default address
- Calculate mathematical expressions (for price difference computations)
- Transfer to human agents when requests cannot be fulfilled

### Non-Goals (Out of Scope)
- Handling multiple users in a single conversation
- Making subjective recommendations or providing opinions
- Creating new orders
- Modifying orders that are already processed, cancelled, or in exchange/return status
- Changing product types during exchanges (e.g., shirt to shoe)
- Calling exchange or modify-items tools more than once per order

---

## AI Agent Design

### Agent Role & Capabilities

**Primary Role:** Retail customer service agent that manages order lifecycle operations and provides account information within strict policy boundaries.

**Core Capabilities:**
- User authentication (by email or name + zip code)
- Order status lookup and detail retrieval
- Product catalog browsing and variant details
- Pending order cancellation with refund processing
- Pending order modification (address, payment, items)
- Delivered order returns with refund
- Delivered order item exchanges
- User address modification
- Mathematical calculations
- Human agent transfer escalation

**Autonomous Actions:**
- Look up user information by email or name + zip
- Retrieve order, product, and user details
- List all product types
- Calculate expressions

**Human-in-the-Loop Actions:**
- Cancel pending order (requires explicit user confirmation)
- Modify pending order address (requires explicit user confirmation)
- Modify pending order items (requires explicit user confirmation)
- Modify pending order payment method (requires explicit user confirmation)
- Return delivered order items (requires explicit user confirmation)
- Exchange delivered order items (requires explicit user confirmation)
- Modify user address (requires explicit user confirmation)

---

## Requirements

### Must-Have Requirements

**REQ-001**: User Authentication
- **Problem to Solve**: Verify user identity before providing account access or taking actions
- **User Story**: As a customer, I need to verify my identity so the agent can securely access my account
- **Acceptance Criteria**:
  - Agent can find user by email address
  - Agent can find user by first name, last name, and zip code (fallback)
  - Authentication must occur at the start of every conversation
  - Even if user provides user_id directly, authentication is still required

**REQ-002**: Order Information Retrieval
- **Problem to Solve**: Customers need to check order status and details
- **User Story**: As a customer, I need to view my order details including status, items, and tracking
- **Acceptance Criteria**:
  - Returns full order details: status, items, address, fulfillments, payment history
  - Only accessible for authenticated user's own orders

**REQ-003**: Product Information Retrieval
- **Problem to Solve**: Customers need product and variant details for exchanges/modifications
- **User Story**: As a customer, I need to see available product variants and their prices
- **Acceptance Criteria**:
  - Can list all 50 product types with names and IDs
  - Can retrieve detailed variant information including options, availability, and price

**REQ-004**: Cancel Pending Order
- **Problem to Solve**: Customers need to cancel orders that haven't been processed yet
- **User Story**: As a customer, I need to cancel my pending order
- **Acceptance Criteria**:
  - Only pending orders can be cancelled
  - Reason must be "no longer needed" or "ordered by mistake"
  - Agent explains cancellation details before proceeding
  - Explicit user confirmation required
  - Gift card refunds applied immediately; other methods refunded in 5-7 business days

**REQ-005**: Modify Pending Order Address
- **Problem to Solve**: Customers need to change shipping address before order ships
- **User Story**: As a customer, I need to update the shipping address on my pending order
- **Acceptance Criteria**:
  - Only pending orders can have address modified
  - Full address (address1, address2, city, state, country, zip) must be provided
  - Explicit user confirmation required

**REQ-006**: Modify Pending Order Items
- **Problem to Solve**: Customers need to change item options (e.g., size, color) before order ships
- **User Story**: As a customer, I need to change item variants in my pending order
- **Acceptance Criteria**:
  - Only pending orders (not already item-modified) can be modified
  - New items must be same product type, different variant, and available
  - Can only be called once per order (changes status to "pending (item modified)")
  - Payment method required for price difference handling
  - Gift card must have sufficient balance for price increases
  - Explicit user confirmation required

**REQ-007**: Modify Pending Order Payment
- **Problem to Solve**: Customers need to change the payment method on a pending order
- **User Story**: As a customer, I need to switch the payment method on my pending order
- **Acceptance Criteria**:
  - Only pending orders can have payment modified
  - New payment method must be different from current
  - Gift card must have sufficient balance to cover full order amount
  - Original payment refunded (immediately for gift card, 5-7 days otherwise)
  - Explicit user confirmation required

**REQ-008**: Return Delivered Order Items
- **Problem to Solve**: Customers need to return items from delivered orders
- **User Story**: As a customer, I need to return items from my delivered order
- **Acceptance Criteria**:
  - Only delivered orders can have items returned
  - Refund must go to original payment method or an existing gift card
  - Items must exist in the order
  - Order status changes to "return requested"
  - User receives follow-up email with return instructions
  - Explicit user confirmation required

**REQ-009**: Exchange Delivered Order Items
- **Problem to Solve**: Customers need to exchange items for different variants
- **User Story**: As a customer, I need to exchange items in my delivered order for different options
- **Acceptance Criteria**:
  - Only delivered orders can have items exchanged
  - New items must be same product type, different variant, and available
  - Can only be called once per order
  - Payment method required for price difference
  - Gift card must have sufficient balance for price increases
  - Order status changes to "exchange requested"
  - Explicit user confirmation required

**REQ-010**: Modify User Default Address
- **Problem to Solve**: Customers need to update their default address on file
- **User Story**: As a customer, I need to update my default shipping address
- **Acceptance Criteria**:
  - Full address must be provided
  - Explicit user confirmation required

**REQ-011**: Transfer to Human Agent
- **Problem to Solve**: Some requests exceed the automated agent's capabilities or policy
- **User Story**: As a customer, I need to be transferred to a human when my issue can't be resolved automatically
- **Acceptance Criteria**:
  - Transfer when user explicitly requests human agent
  - Transfer when request cannot be solved with available tools/policy
  - Summary of user's issue is passed to human agent

---

## AI Guardrails & Safety

- Agent must authenticate user at conversation start before any data access or actions
- Agent can only help one user per conversation
- Agent must deny requests related to other users
- Agent must obtain explicit confirmation (yes/no) before any database-modifying action
- Agent must not make up information or give subjective recommendations
- Agent makes at most one tool call per turn
- Agent must not respond to user and make a tool call in the same turn
- Agent must deny requests that violate policy
- Exchange/modify-items can only be called once per order — agent must collect all changes before executing

---

## Domain Model

### User
- **user_id**: Unique identifier (e.g., 'sara_doe_496')
- **name**: First and last name
- **email**: Email address
- **address**: Default shipping address (address1, address2, city, state, country, zip)
- **payment_methods**: Credit cards, gift cards, PayPal accounts
- **orders**: List of associated order IDs

### Product
- **product_id**: Unique identifier (e.g., '6086499569')
- **name**: Product name
- **variants**: Dictionary of item variants with options, availability, and price

### Order
- **order_id**: Unique identifier (e.g., '#W0000000')
- **user_id**: Owner of the order
- **status**: pending | processed | delivered | cancelled | pending (item modified) | exchange requested | return requested
- **address**: Shipping address
- **items**: List of ordered items (name, product_id, item_id, price, options)
- **fulfillments**: Tracking information
- **payment_history**: List of payment/refund transactions
- **cancel_reason**: Optional (no longer needed | ordered by mistake)
- **exchange_items/exchange_new_items**: Optional exchange details
- **return_items**: Optional return details

### Payment Methods
- **Credit Card**: brand, last four digits
- **Gift Card**: balance (updated immediately on refunds)
- **PayPal**: account reference
