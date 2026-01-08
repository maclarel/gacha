#!/usr/bin/env python3
"""
Gacha - A minimal, configuration-driven webserver for serving files over HTTP(S)
with fine-grained access control rules.
"""

import os
import sys
import yaml
import argparse
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import ipaddress

# Logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("gacha.log"),
                        logging.StreamHandler()
                    ])

# Constants
CHUNK_SIZE = 8192  # File streaming chunk size in bytes


class RuleValidator:
    """Validates requests against defined rules."""

    def __init__(self, rule: Dict[str, Any], rule_id: str):
        self.rule = rule
        self.rule_id = rule_id
        self.path = rule.get('path')
        self.request_uri = rule.get('request_uri')
        self.header = rule.get('header')
        self.header_value = rule.get('header_value', [])
        self.user_agent = rule.get('user_agent')
        self.eol = rule.get('eol')
        self.serve_once = rule.get('serve_once', False)
        self.source_ip = rule.get('source_ip', [])

        # Ensure header_value is a list
        if self.header_value and not isinstance(self.header_value, list):
            self.header_value = [self.header_value]
        
        # Ensure source_ip is a list
        if self.source_ip and not isinstance(self.source_ip, list):
            self.source_ip = [self.source_ip]

    def validate(self, request_path: str, headers: Dict[str, str], client_address: tuple = None) -> bool:
        """
        Validate a request against this rule.
        All conditions must be met (logical AND).

        Args:
            request_path: The URI path being requested
            headers: Dictionary of request headers
            client_address: Tuple of (ip, port) from the client connection

        Returns:
            True if request matches all rule conditions, False otherwise
        """
        # Check request URI (required)
        if self.request_uri != request_path:
            return False

        # Check source IP if specified
        if self.source_ip:
            # Extract client IP from X-Forwarded-For header or client_address
            client_ip = None
            
            # Prefer X-Forwarded-For if present (proxy scenario)
            x_forwarded_for = headers.get('X-Forwarded-For', '').strip()
            if x_forwarded_for:
                # X-Forwarded-For can contain multiple IPs, use the first one (original client)
                client_ip = x_forwarded_for.split(',')[0].strip()
            elif client_address:
                # Use direct connection IP
                client_ip = client_address[0]
            
            if not client_ip:
                logging.debug("No client IP available for source_ip check")
                return False
            
            # Validate IP against allowed CIDRs (OR logic for multiple CIDRs)
            ip_matched = False
            try:
                client_ip_obj = ipaddress.ip_address(client_ip)
                for cidr in self.source_ip:
                    try:
                        network = ipaddress.ip_network(cidr, strict=False)
                        if client_ip_obj in network:
                            ip_matched = True
                            logging.debug(f"Client IP {client_ip} matched CIDR {cidr}")
                            break
                    except (ValueError, TypeError) as e:
                        logging.warning(f"Invalid CIDR notation in rule: {cidr} - {e}")
                        continue
                
                if not ip_matched:
                    logging.debug(f"Client IP {client_ip} did not match any allowed CIDRs {self.source_ip}")
                    return False
            except ValueError:
                logging.debug(f"Invalid client IP address: {client_ip}")
                return False

        # Check EOL date if specified
        if self.eol:
            try:
                # Handle both string and datetime objects from YAML
                if isinstance(self.eol, str):
                    # Handle ISO 8601 format with 'Z' suffix for UTC
                    eol_str = self.eol
                    if eol_str.endswith('Z'):
                        # Replace Z with +00:00 and remove milliseconds if present
                        eol_str = eol_str[:-1]
                        if '.' in eol_str:
                            # Remove milliseconds (.000)
                            eol_str = eol_str.split('.')[0]
                        eol_str = eol_str + '+00:00'
                    eol_date = datetime.fromisoformat(eol_str)
                else:
                    # Already a datetime object
                    eol_date = self.eol

                # Compare with timezone-aware current time
                now = datetime.now(timezone.utc)
                if now >= eol_date:
                    logging.debug(f"Not serving file as {now} > {eol_date}")
                    return False
                else:
                    logging.debug(f"Current time is < {eol_date}")
            except (ValueError, AttributeError, TypeError):
                # Log generic warning without exposing sensitive details
                logging.critical("Warning: Invalid EOL date format in rule")
                return False

        # Check user agent if specified
        if self.user_agent:
            request_user_agent = headers.get('User-Agent', '')
            if request_user_agent != self.user_agent:
                logging.debug(f"User agent {request_user_agent} did not match {self.user_agent}")
                return False
            else:
                logging.debug(f"User agent {request_user_agent} matched {self.user_agent}")

        # Check header if specified
        if self.header:
            header_present = self.header in headers
            if not header_present:
                return False

            # Check header value if specified (OR logic for multiple values)
            if self.header_value:
                request_header_value = headers.get(self.header, '')
                if request_header_value not in self.header_value:
                    logging.debug(f"Header {request_header_value} did not match {self.header_value}")
                    return False
                else:
                    logging.debug(f"Header {request_header_value} matched {self.header_value}")

        return True


class GachaHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Gacha server."""

    # Class variables are shared across instances but only read, not modified
    # This is safe because BaseHTTPRequestHandler creates new instances per request
    rules: List[RuleValidator] = []
    base_path: str = ""
    served_once_rules: set = set()  # Track rules that have been served with serve_once=True

    def do_GET(self):
        """Handle GET requests."""
        # Get request headers
        headers = {k: v for k, v in self.headers.items()}

        # Find matching rule
        matching_rule = None
        for rule in self.rules:
            # Check if rule has serve_once and has already been served
            if rule.serve_once and rule.rule_id in self.served_once_rules:
                logging.info(f"Request denied: Rule {rule.rule_id} has already been served once and cannot be served again (serve_once=True)")
                continue
            
            if rule.validate(self.path, headers, self.client_address):
                matching_rule = rule
                break

        if not matching_rule:
            self.send_error(404, "Not Found")
            return

        # Construct file path and validate against directory traversal
        try:
            # Use pathlib for robust cross-platform path validation
            base_path_resolved = Path(self.base_path).resolve()
            file_path_resolved = (base_path_resolved / matching_rule.path).resolve()

            # Ensure the file path is within the base path
            if not file_path_resolved.is_relative_to(base_path_resolved):
                self.send_error(404, "Not Found")
                return

            file_path = str(file_path_resolved)
        except (ValueError, OSError):
            self.send_error(404, "Not Found")
            return

        # Check if file exists
        if not os.path.isfile(file_path):
            self.send_error(404, "Not Found")
            return

        # Serve the file with streaming for memory efficiency
        try:
            file_size = os.path.getsize(file_path)

            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', file_size)
            self.end_headers()

            # Stream file in chunks
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            
            # Mark rule as served only after successful file serving
            if matching_rule.serve_once:
                self.served_once_rules.add(matching_rule.rule_id)
                logging.info(f"Successfully served file for rule {matching_rule.rule_id} with serve_once=True - this rule will not be available for future requests")
        except Exception as e:
            # Log the error internally but don't expose details to client
            logging.info(f"Error serving file: {e}")
            self.send_error(500, "Internal Server Error")

    def log_message(self, format, *args):
        """Custom log message format."""
        logging.info("%s - %s" %
                        (self.address_string(),
                         format % args))


def load_config(config_path: str) -> Dict[str, Any]:
    """
    Load main configuration from YAML file.

    Args:
        config_path: Path to config.yaml

    Returns:
        Dictionary with configuration values
    """
    try:
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)

        config = data.get('config', {})

        # Validate required fields
        if 'hostname' not in config:
            raise ValueError("Missing required 'hostname' in config")
        if 'listen_port' not in config:
            raise ValueError("Missing required 'listen_port' in config")

        return config
    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing configuration file: {e}")


def load_rules(rules_dir: str) -> List[RuleValidator]:
    """
    Load all rule files from the rules directory.

    Args:
        rules_dir: Path to rules directory

    Returns:
        List of RuleValidator objects
    """
    rules = []

    if not os.path.isdir(rules_dir):
        logger.critical(f"Warning: Rules directory not found: {rules_dir}")
        return rules

    for filename in os.listdir(rules_dir):
        if filename.endswith(('.yaml', '.yml')):
            rule_path = os.path.join(rules_dir, filename)
            try:
                with open(rule_path, 'r') as f:
                    data = yaml.safe_load(f)

                rule_data = data.get('rule', {})

                # Validate required fields
                if 'path' not in rule_data:
                    logging.critical(f"Warning: Missing 'path' in rule file: {filename}")
                    continue
                if 'request_uri' not in rule_data:
                    logging.critical(f"Warning: Missing 'request_uri' in rule file: {filename}")
                    continue

                # Use filename as rule_id for tracking serve_once
                rules.append(RuleValidator(rule_data, filename))
                logging.info(f"Loaded rule from {filename}")
            except Exception as e:
                logging.error(f"Warning: Error loading rule file {filename}: {e}")

    return rules


def main():
    """Main entry point for Gacha server."""
    parser = argparse.ArgumentParser(description='Gacha - Configuration-driven file server')
    parser.add_argument('--config', default='config.yaml', help='Path to configuration file')
    parser.add_argument('--base-path', default='.', help='Base path for the server')
    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        logging.error(f"Error loading configuration: {e}")
        sys.exit(1)

    # Load rules
    rules_dir = os.path.join(args.base_path, 'rules')
    rules = load_rules(rules_dir)

    if not rules:
        logging.warning("Warning: No valid rules loaded. No files will be served.")

    # Set up handler with rules and base path
    GachaHandler.rules = rules
    GachaHandler.base_path = args.base_path

    # Create server
    hostname = config['hostname']
    port = config['listen_port']
    server_address = (hostname, port)

    httpd = HTTPServer(server_address, GachaHandler)

    # Configure TLS if certificates are provided
    tls_cert = config.get('tls_cert')
    tls_key = config.get('tls_key')
    tls_enabled = False

    if tls_cert and tls_key:
        if not os.path.isfile(tls_cert):
            logging.critical(f"Warning: TLS certificate not found: {tls_cert}")
        elif not os.path.isfile(tls_key):
            logging.critical(f"Warning: TLS key not found: {tls_key}")
        else:
            try:
                # Use create_default_context for better security defaults
                context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                # Ensure minimum TLS 1.2
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(tls_cert, tls_key)
                httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
                tls_enabled = True
                logging.info(f"TLS enabled with cert: {tls_cert}")
            except Exception as e:
                logging.error(f"Error setting up TLS: {e}")
                sys.exit(1)

    protocol = "https" if tls_enabled else "http"
    logging.info(f"Starting Gacha server on {protocol}://{hostname}:{port}")
    logging.info(f"Loaded {len(rules)} rule(s)")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
        httpd.shutdown()
        sys.exit(0)


if __name__ == '__main__':
    main()
