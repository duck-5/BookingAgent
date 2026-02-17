# Booking Rules & Constraints

This document outlines the high-level business rules that valid reservations must follow.

## 1. Booking Window
*   **Release Rule**: A booking slot for any given time `T` becomes available for reservation exactly **7 days in advance**, but shifted by **1 hour**.
    *   *Formula*: `Opening Time = Target Slot Time - 7 Days + 1 Hour`.
    *   *Example*: To book a room for **Friday at 10:00 AM**, the reservation can only be made starting from the **previous Friday at 11:00 AM**.

## 2. Reservation Constraints
*   **Duration**: Bookings are typically made in hourly intervals.
*   **Quotas**: Individual user accounts have limits on how many hours they can book per day or week. The system is aware of this and must rotate users to fulfill longer requirements.
*   **Consecutive Slots**: If a user needs to book multiple contiguous hours (e.g., 10:00-12:00), the system will attempt to book them sequentially. If only the first hour is secured, the system handles existing multi-hour event splitting.

## 3. Resource Priority
*   **Room Hierarchy**: Not all rooms are equal. There is a predefined list of "High Priority" rooms (e.g., specific floors or wings) that must be attempted first. "Low Priority" rooms are only used as a fallback.

## 4. User Account Usage
*   **Rotation**: To support intensive study schedules that exceed single-user quotas, multiple accounts are treated as a shared pool.
*   **Optimization**: The system prioritizes using the account that successfully secured the last slot to maintain session continuity, only switching if that account hits a limit.
