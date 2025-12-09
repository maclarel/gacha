#!/usr/bin/env python3
"""
Gacha - A minimal, configuration-driven webserver for serving files over HTTP(S)
with fine-grained access control rules.
"""

import os
import sys
import yaml
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

# Constants
CHUNK_SIZE = 8192  # File streaming chunk size in bytes


class RuleValidator:
    """Validates requests against defined rules."""
    
    def __init__(self, rule: Dict[str, Any]):
        self.rule = rule
        self.path = rule.get('path')
        self.request_uri = rule.get('request_uri')
        self.header = rule.get('header')
        self.header_value = rule.get('header_value', [])
        self.user_agent = rule.get('user_agent')
        self.eol = rule.get('eol')
        
        # Ensure header_value is a list
        if self.header_value and not isinstance(self.header_value, list):
            self.header_value = [self.header_value]
    
    def validate(self, request_path: str, headers: Dict[str, str]) -> bool:
        """
        Validate a request against this rule.
        All conditions must be met (logical AND).
        
        Args:
            request_path: The URI path being requested
            headers: Dictionary of request headers
            
        Returns:
            True if request matches all rule conditions, False otherwise
        """
        # Check request URI (required)
        if self.request_uri != request_path:
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
                    return False
            except (ValueError, AttributeError, TypeError):
                # Log generic warning without exposing sensitive details
                print("Warning: Invalid EOL date format in rule")
                return False
        
        # Check user agent if specified
        if self.user_agent:
            request_user_agent = headers.get('User-Agent', '')
            if request_user_agent != self.user_agent:
                return False
        
        # Check header if specified
        if self.header:
            header_present = self.header in headers
            if not header_present:
                return False
            
            # Check header value if specified (OR logic for multiple values)
            if self.header_value:
                request_header_value = headers.get(self.header, '')
                if request_header_value not in self.header_value:
                    return False
        
        return True


class GachaHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Gacha server."""
    
    # Class variables are shared across instances but only read, not modified
    # This is safe because BaseHTTPRequestHandler creates new instances per request
    rules: List[RuleValidator] = []
    base_path: str = ""
    
    def do_GET(self):
        """Handle GET requests."""
        # Get request headers
        headers = {k: v for k, v in self.headers.items()}
        
        # Find matching rule
        matching_rule = None
        for rule in self.rules:
            if rule.validate(self.path, headers):
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
            self.send_error(404, "File Not Found")
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
        except Exception as e:
            # Log the error internally but don't expose details to client
            print(f"Error serving file: {e}")
            self.send_error(500, "Internal Server Error")
    
    def log_message(self, format, *args):
        """Custom log message format."""
        sys.stdout.write("%s - - [%s] %s\n" %
                        (self.address_string(),
                         self.log_date_time_string(),
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
        print(f"Warning: Rules directory not found: {rules_dir}")
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
                    print(f"Warning: Missing 'path' in rule file: {filename}")
                    continue
                if 'request_uri' not in rule_data:
                    print(f"Warning: Missing 'request_uri' in rule file: {filename}")
                    continue
                
                rules.append(RuleValidator(rule_data))
                print(f"Loaded rule from {filename}")
            except Exception as e:
                print(f"Warning: Error loading rule file {filename}: {e}")
    
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
        print(f"Error loading configuration: {e}")
        sys.exit(1)
    
    # Load rules
    rules_dir = os.path.join(args.base_path, 'rules')
    rules = load_rules(rules_dir)
    
    if not rules:
        print("Warning: No valid rules loaded. No files will be served.")
    
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
            print(f"Warning: TLS certificate not found: {tls_cert}")
        elif not os.path.isfile(tls_key):
            print(f"Warning: TLS key not found: {tls_key}")
        else:
            try:
                # Use create_default_context for better security defaults
                context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                # Ensure minimum TLS 1.2
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(tls_cert, tls_key)
                httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
                tls_enabled = True
                print(f"TLS enabled with cert: {tls_cert}")
            except Exception as e:
                print(f"Error setting up TLS: {e}")
                sys.exit(1)
    
    protocol = "https" if tls_enabled else "http"
    print(f"Starting Gacha server on {protocol}://{hostname}:{port}")
    print(f"Loaded {len(rules)} rule(s)")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.shutdown()
        sys.exit(0)


if __name__ == '__main__':
    main()
