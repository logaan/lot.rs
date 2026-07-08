//! LoT identifiers.
//!
//! Things (tasks) and updates are both identified by a URI of the form
//! `lot:<id>`, where `<id>` is a version 7 UUID encoded in base62. The base62
//! encoding of a 128-bit UUID is always 22 characters (zero-padded), so a full
//! id is 26 characters including the `lot:` scheme.

use uuid::Uuid;

/// The URI scheme prefix shared by every LoT id.
pub const PREFIX: &str = "lot:";

/// The number of base62 digits needed to encode a 128-bit value.
const ENCODED_LEN: usize = 22;

/// base62 alphabet: digits, then uppercase, then lowercase.
const ALPHABET: &[u8; 62] = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

/// Generate a fresh id: `lot:` followed by a base62-encoded UUIDv7.
pub fn new() -> String {
    format!("{PREFIX}{}", encode(Uuid::now_v7().as_u128()))
}

/// Normalise a user-supplied id to canonical `lot:<id>` form, adding the
/// `lot:` scheme if the caller omitted it. Surrounding whitespace is trimmed.
pub fn normalize(input: &str) -> String {
    let trimmed = input.trim();
    if trimmed.starts_with(PREFIX) {
        trimmed.to_string()
    } else {
        format!("{PREFIX}{trimmed}")
    }
}

/// Encode a 128-bit value as a fixed-width, zero-padded 22-character base62
/// string.
fn encode(mut n: u128) -> String {
    let mut buf = [b'0'; ENCODED_LEN];
    let mut i = ENCODED_LEN;
    while n > 0 {
        i -= 1;
        buf[i] = ALPHABET[(n % 62) as usize];
        n /= 62;
    }
    // SAFETY: every byte is drawn from the ASCII `ALPHABET`.
    String::from_utf8(buf.to_vec()).expect("base62 output is valid ASCII")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_zero_as_padded_zeros() {
        assert_eq!(encode(0), "0".repeat(ENCODED_LEN));
    }

    #[test]
    fn encodes_small_values() {
        assert_eq!(encode(1), format!("{}1", "0".repeat(ENCODED_LEN - 1)));
        assert_eq!(encode(61), format!("{}z", "0".repeat(ENCODED_LEN - 1)));
        assert_eq!(encode(62), format!("{}10", "0".repeat(ENCODED_LEN - 2)));
    }

    #[test]
    fn max_value_fits_in_22_chars() {
        assert_eq!(encode(u128::MAX).len(), ENCODED_LEN);
    }

    #[test]
    fn new_id_is_26_chars_with_prefix() {
        let id = new();
        assert!(id.starts_with(PREFIX));
        assert_eq!(id.len(), PREFIX.len() + ENCODED_LEN);
        assert_eq!(id.len(), 26);
    }

    #[test]
    fn new_ids_are_unique() {
        assert_ne!(new(), new());
    }

    #[test]
    fn normalize_adds_missing_prefix() {
        assert_eq!(normalize("abc"), "lot:abc");
        assert_eq!(normalize("  abc  "), "lot:abc");
    }

    #[test]
    fn normalize_keeps_existing_prefix() {
        assert_eq!(normalize("lot:abc"), "lot:abc");
        assert_eq!(normalize("  lot:abc "), "lot:abc");
    }
}
