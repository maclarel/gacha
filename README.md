# Gacha

Opsec-friendly file server over HTTP(S) with fine-grained access control.

## Overview

Gacha is a minimal, configuration-driven webserver that serves files over HTTP or HTTPS based on flexible, per-file access control rules. Files are only served when all specified conditions are met, including request URI, headers, user agent, and expiration dates.

Shoutouts to @rookuu for the inspiration <3

## Features

- **Configuration-driven**: Centralized server configuration and per-file access rules
- **Fine-grained access control**: Control file access based on:
  - Request URI (required)
  - HTTP headers and their values (optional, with OR logic for multiple values)
  - User agent strings (optional)
  - Expiration dates (optional)
- **TLS Support**: Optional HTTPS with custom certificates
- **Simple setup**: YAML configuration files, minimal dependencies

## Installation

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Quick Start

1. Create a `config.yaml` file with your server configuration:
   ```yaml
   config:
     hostname: localhost
     listen_port: 8080
     # Optional TLS configuration
     # tls_cert: /path/to/cert.cer
     # tls_key: /path/to/cert.key
   ```

2. Create `files/` and `rules/` directories:
   ```bash
   mkdir -p files rules
   ```

3. Place files to serve in the `files/` directory:
   ```bash
   echo "Hello, World!" > files/myfile.txt
   ```

4. Create a rule file in `rules/` (e.g., `rules/myfile.yaml`):
   ```yaml
   rule:
     path: files/myfile.txt
     request_uri: /hello
   ```

5. Run the server:
   ```bash
   python3 gacha.py
   ```

6. Access your file:
   ```bash
   curl http://localhost:8080/hello
   ```

## Configuration

### Main Configuration (`config.yaml`)

The main configuration file defines server-level settings:

```yaml
config:
  hostname: foo.bar.com      # Required: hostname to listen on
  listen_port: 8080           # Required: port to listen on
  tls_cert: /path/to/cert.cer # Optional: TLS certificate path
  tls_key: /path/to/cert.key  # Optional: TLS key path
```

### Rule Files (`rules/*.yaml`)

Each file in the `files/` directory can have a corresponding rule file in `rules/`. Rules define the conditions under which a file will be served.

#### Required Fields

- `path`: Path to the file relative to the server's base path
- `request_uri`: The URI path that must be requested

#### Optional Fields

- `header`: Header name that must be present in the request
- `header_value`: Single value or list of acceptable header values (OR logic)
- `user_agent`: Exact user agent string required
- `eol`: Expiration date/time in ISO 8601 format (file won't be served after this date)
- `serve_once`: Boolean value (default: False). When set to True, the file will only be served once during the server's lifetime. Subsequent requests will receive a 404 error. This setting does not persist across server restarts.

#### Example: Complex Rule

```yaml
rule:
  path: files/sensitive-data.bin
  request_uri: /api/v1/data
  header: X-API-Key
  header_value:
    - key-1
    - key-2
  user_agent: MyApp/1.0
  eol: 2026-11-27T00:30:00.000Z
```

This rule requires:
- Request URI must be `/api/v1/data`
- AND `X-API-Key` header must be present
- AND `X-API-Key` value must be either `key-1` OR `key-2`
- AND User agent must be exactly `MyApp/1.0`
- AND Current date must be before `2026-11-27T00:30:00Z`

#### Example: Simple Rule

```yaml
rule:
  path: files/public.txt
  request_uri: /public
```

This rule only requires the request URI to be `/public`.

## Usage

```bash
python3 gacha.py [--config CONFIG] [--base-path BASE_PATH]
```

Options:
- `--config`: Path to configuration file (default: `config.yaml`)
- `--base-path`: Base path for the server (default: `.`)

## Security Considerations

- All conditions in a rule are evaluated with AND logic (all must be true)
- Multiple `header_value` entries are evaluated with OR logic (any can match)
- Files without corresponding rules in `rules/` directory will NOT be served
- Request URIs must match exactly (no pattern matching or wildcards)
- User agent strings must match exactly (no partial matching)
- The server returns 404 for all unauthorized requests (does not reveal why access was denied)

## Examples

### Example 1: Time-limited Download

Serve a file only until a specific date:

```yaml
rule:
  path: files/limited-offer.pdf
  request_uri: /offer
  eol: 2024-12-31T23:59:59.000Z
```

### Example 2: API Key Authentication

Serve a file only with valid API key:

```yaml
rule:
  path: files/api-response.json
  request_uri: /api/data
  header: Authorization
  header_value:
    - Bearer token123
    - Bearer token456
```

### Example 3: User Agent Restriction

Serve a file only to specific application:

```yaml
rule:
  path: files/app-data.bin
  request_uri: /data
  user_agent: MyApp/2.0 (Linux; Android 10)
```

### Example 4: Single-Use File

Serve a file only once (useful for one-time downloads, burn-after-reading scenarios):

```yaml
rule:
  path: files/secret-data.bin
  request_uri: /onetime
  serve_once: True
```

After the first successful request, all subsequent requests to `/onetime` will receive a 404 error, even if all other conditions match. The file becomes available again after restarting the server.

## License

See LICENSE file for details.
