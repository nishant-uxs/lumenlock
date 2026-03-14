# Changelog

## [Unreleased] - 2024

### Fixed
- **Critical Bug**: Fixed wallet creation bug where wallets were being created for the wrong user (using `User.objects.first()` instead of `request.user`)
- Added password validation (minimum 8 characters) for wallet creation
- Fixed potential wallet orphaning by cleaning up on friendbot funding failure
- Improved error handling with proper HTTP status codes across all endpoints

### Added
- **Transaction History Feature**: New endpoint and UI to view transaction history
  - Displays last 20 transactions with full details
  - Shows transaction type, amount, sender, receiver, and timestamp
  - Links to Stellar Expert for detailed transaction inspection
  - Beautiful table UI with responsive design
- Comprehensive input validation for Stellar addresses
- Input validation for transaction amounts (must be positive numbers)
- Password verification for transactions (returns proper error if incorrect)
- Timeout handling for external API calls
- Better error messages for users

### Improved
- Enhanced security with proper password validation
- Better error handling throughout the application
- Improved user feedback with detailed error messages
- Added validation helper function `is_valid_stellar_address()`
- Improved frontend error handling with toast notifications
- Better exception handling for Stellar SDK operations
- Changed from array indexing `[0]` to `.first()` for safer database queries

### Security
- Validates transaction passwords before decryption
- Validates Stellar addresses before API calls
- Prevents invalid transactions with comprehensive validation
- Added request timeouts to prevent hanging connections
