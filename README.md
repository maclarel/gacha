# Gacha

Opsec-friendly file server over HTTP(S) with fine-grained access control.

## Overview

Gacha is a minimal, configuration-driven webserver that serves files over HTTP or HTTPS based on flexible, per-file access control rules. Files are only served when all specified conditions are met, including request URI, headers, user agent, and expiration dates.

Shoutouts to @rookuu for the inspiration <3

## Features

- **Configuration-driven**: Centralized server configuration and per-file access rules
- **Automatic rule reloading**: Monitors rule files for changes and reloads automatically without server restart
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

## Running with Docker

Gacha can be easily run using Docker, which eliminates the need to install Python and dependencies locally.

> [!IMPORTANT]
> Ensure that `hostname` in `config.yaml` is set to `0.0.0.0` when running in Docker otherwise the default `localhost` will make the service inaccessible from outside of the container.
> If using Host-mode networking you can instead use the hostname or desired FQDN that would be relevant for the host machine.

### Prerequisites

- Docker installed on your system ([Get Docker](https://docs.docker.com/get-docker/))
- Docker Compose installed (included with Docker Desktop, or install separately for Linux)

### Using Docker Compose (Recommended)

1. Clone this repository
2. Modify rules, add files, and update configuration as desired
3. Start the server using Docker Compose:
   ```bash
   docker-compose up -d
   ```

5. Access your files at `http://localhost` (maps to port 80) or `https://localhost` (maps to port 443 if TLS is configured)

6. View logs:
   ```bash
   docker-compose logs -f
   ```

7. Stop the server:
   ```bash
   docker-compose down
   ```

**Note**: The `docker-compose.yml` file maps:
- Container port 8080 → Host port 80 (HTTP)
- Container port 8443 → Host port 443 (HTTPS)
- Local `./files` → Container `/app/files`
- Local `./rules` → Container `/app/rules`
- Local `./config.yaml` → Container `/app/config.yaml`

For TLS/HTTPS, uncomment the certs volume mount in `docker-compose.yml` and ensure your certificate paths in `config.yaml` point to `/app/certs/`.

### Using Docker (Without Docker Compose)

1. Build the Docker image:
   ```bash
   docker build -t gacha .
   ```

2. Run the container:
   ```bash
   docker run -d \
     --name gacha-server \
     -p 80:8080 \
     -v $(pwd)/files:/app/files \
     -v $(pwd)/rules:/app/rules \
     -v $(pwd)/config.yaml:/app/config.yaml \
     gacha
   ```

   For HTTPS support, add the certificate port and volume:
   ```bash
   docker run -d \
     --name gacha-server \
     -p 80:8080 \
     -p 443:8443 \
     -v $(pwd)/files:/app/files \
     -v $(pwd)/rules:/app/rules \
     -v $(pwd)/config.yaml:/app/config.yaml \
     -v $(pwd)/certs:/app/certs \
     gacha
   ```

3. View logs:
   ```bash
   docker logs -f gacha-server
   ```

4. Stop and remove the container:
   ```bash
   docker stop gacha-server
   docker rm gacha-server
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
  watch_rules: true           # Optional: Enable rule file monitoring (default: true)
  watch_interval: 3.0         # Optional: Rule file check interval in seconds (default: 3.0)
```

#### Rule File Monitoring

By default, Gacha monitors the rules directory for changes and automatically reloads rules when files are added, modified, or deleted. This allows you to update access control rules without restarting the server.

- **`watch_rules`**: Set to `false` to disable automatic rule reloading (default: `true`)
- **`watch_interval`**: How often to check for changes in seconds (default: `3.0`)

During rule reloading:
- Rules remain enforced throughout the reload process (no downtime)
- If new rules fail to load due to syntax errors, the old rules are kept and an error is logged
- The `serve_once` tracking is reset when rules are successfully reloaded


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
- `source_ip`: Single CIDR notation or list of CIDR notations specifying allowed source IP addresses (OR logic). Supports both IPv4 and IPv6. If the `X-Forwarded-For` header is present, its value (the original client IP) is used; otherwise, the direct client connection IP is used.

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
  source_ip:
    - 192.168.1.0/24
    - 10.0.0.50/32
```

This rule requires:
- Request URI must be `/api/v1/data`
- AND `X-API-Key` header must be present
- AND `X-API-Key` value must be either `key-1` OR `key-2`
- AND User agent must be exactly `MyApp/1.0`
- AND Current date must be before `2026-11-27T00:30:00Z`
- AND Source IP must be from `192.168.1.0/24` OR exactly `10.0.0.50`

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

### Example 5: IP Address Restriction

Serve a file only from specific IP addresses or networks:

```yaml
rule:
  path: files/internal-data.txt
  request_uri: /internal
  source_ip: 192.168.1.0/24
```

This restricts access to clients from the 192.168.1.0/24 network.

### Example 6: Multiple IP Networks

Allow access from multiple networks (OR logic):

```yaml
rule:
  path: files/multi-site-data.json
  request_uri: /data
  source_ip:
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 192.168.0.0/16
```

This allows access from any of the three private network ranges. If a request comes through a proxy, the `X-Forwarded-For` header value is used to determine the client's IP address.

## License

See LICENSE file for details.
