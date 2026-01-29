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
from typing import Dict, List, Optional, Any, TYPE_CHECKING
import ipaddress
import threading
import time

if TYPE_CHECKING:
    from typing import Callable

# Logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("gacha.log"),
                        logging.StreamHandler()
                    ])

# Constants
CHUNK_SIZE = 8192  # File streaming chunk size in bytes
DEFAULT_POLL_INTERVAL = 3.0  # Default polling interval in seconds


class RuleFileMonitor:
    """Monitors rule files for changes and reloads them automatically."""
    
    def __init__(self, rules_dir: str, poll_interval: float = DEFAULT_POLL_INTERVAL):
        """
        Initialize the rule file monitor.
        
        Args:
            rules_dir: Directory containing rule files
            poll_interval: How often to check for changes (in seconds)
        """
        self.rules_dir = rules_dir
        self.poll_interval = poll_interval
        self.file_mtimes: Dict[str, float] = {}
        self.rules: List[Any] = []  # List of RuleValidator objects
        self.rules_lock = threading.Lock()
        self.monitor_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.reload_callback = None
        
    def get_rule_files(self) -> Dict[str, float]:
        """
        Get all rule files and their modification times.
        
        Returns:
            Dictionary mapping file paths to modification times
        """
        rule_files = {}
        if not os.path.isdir(self.rules_dir):
            return rule_files
            
        for filename in os.listdir(self.rules_dir):
            if filename.endswith(('.yaml', '.yml')):
                filepath = os.path.join(self.rules_dir, filename)
                try:
                    mtime = os.path.getmtime(filepath)
                    rule_files[filepath] = mtime
                except OSError:
                    continue
        return rule_files
    
    def get_changed_rule_files(self) -> set:
        """
        Get the set of rule filenames that have been added, modified, or deleted.
        
        Returns:
            Set of filenames (basenames only) that changed
        """
        current_files = self.get_rule_files()
        changed_files = set()
        
        # Check for new or modified files
        for filepath, mtime in current_files.items():
            filename = os.path.basename(filepath)
            if filepath not in self.file_mtimes or self.file_mtimes[filepath] != mtime:
                changed_files.add(filename)
        
        # Check for deleted files
        for filepath in self.file_mtimes:
            if filepath not in current_files:
                filename = os.path.basename(filepath)
                changed_files.add(filename)
        
        return changed_files
    
    def has_changes(self) -> bool:
        """
        Check if any rule files have been added, removed, or modified.
        
        Returns:
            True if changes detected, False otherwise
        """
        current_files = self.get_rule_files()
        
        # Check if files were added or removed
        if set(current_files.keys()) != set(self.file_mtimes.keys()):
            return True
        
        # Check if any files were modified
        for filepath, mtime in current_files.items():
            if filepath not in self.file_mtimes or self.file_mtimes[filepath] != mtime:
                return True
        
        return False
    
    def reload_rules(self):
        """Reload rules from the rules directory."""
        logging.info("Reloading rules due to file changes...")
        try:
            # Identify which rule files actually changed
            changed_files = self.get_changed_rule_files()
            
            new_rules = load_rules(self.rules_dir)
            
            # Update rules atomically with lock
            with self.rules_lock:
                self.rules = new_rules
                # Update file_mtimes only on successful load
                self.file_mtimes = self.get_rule_files()
            
            # Call the reload callback if set (pass a copy to prevent modifications)
            # Also pass the set of changed rule filenames
            if self.reload_callback:
                self.reload_callback(new_rules.copy(), changed_files)
            
            logging.info(f"Reloaded {len(new_rules)} rule(s). Changed files: {', '.join(changed_files) if changed_files else 'none'}")
        except Exception as e:
            logging.error(f"Failed to reload rules: {e}. Keeping existing rules.")
            # Don't update file_mtimes so we don't retry immediately
            # The next poll will check again
    
    def monitor_loop(self):
        """Main monitoring loop that runs in a separate thread."""
        logging.info(f"Started rule file monitoring (polling every {self.poll_interval}s)")
        
        while not self.stop_event.is_set():
            try:
                if self.has_changes():
                    self.reload_rules()
            except Exception as e:
                logging.error(f"Error during rule monitoring: {e}")
            
            # Sleep in smaller intervals to allow quick shutdown
            for _ in range(int(self.poll_interval * 10)):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)
    
    def start(self, initial_rules: List[Any], reload_callback=None):
        """
        Start monitoring rule files in a background thread.
        
        Args:
            initial_rules: Initial list of rules
            reload_callback: Function to call when rules are reloaded
        """
        with self.rules_lock:
            self.rules = initial_rules
            self.file_mtimes = self.get_rule_files()
        
        self.reload_callback = reload_callback
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop(self):
        """Stop the monitoring thread."""
        if self.monitor_thread and self.monitor_thread.is_alive():
            logging.info("Stopping rule file monitor...")
            self.stop_event.set()
            self.monitor_thread.join(timeout=2.0)
    
    def get_rules(self) -> List[Any]:
        """
        Get the current rules in a thread-safe manner.
        
        Returns:
            Current list of rules
        """
        with self.rules_lock:
            return self.rules.copy()


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

    def validate(self, request_path: str, headers: Dict[str, str], client_address: tuple = None, config: Dict[str, Any] = None) -> bool:
        """
        Validate a request against this rule.
        All conditions must be met (logical AND).

        Args:
            request_path: The URI path being requested
            headers: Dictionary of request headers
            client_address: Tuple of (ip, port) from the client connection
            config: Optional server configuration for X-Forwarded-For handling

        Returns:
            True if request matches all rule conditions, False otherwise
        """
        # Check request URI (required)
        if self.request_uri != request_path:
            return False

        # Check source IP if specified
        if self.source_ip:
            # Default config if not provided
            if config is None:
                config = {}
            
            use_xff = config.get('use_xff', False)
            xff_upstream_ip = config.get('xff_upstream_ip', None)
            
            # Extract client IP from connection or X-Forwarded-For header
            client_ip = None
            actual_source_ip = client_address[0] if client_address else None
            
            # Determine which IP to validate
            # If use_xff is enabled and the request comes from the trusted upstream IP,
            # then check if X-Forwarded-For header is present
            if use_xff and xff_upstream_ip and actual_source_ip:
                # Check if the actual connection is from the trusted upstream IP
                try:
                    actual_source_obj = ipaddress.ip_address(actual_source_ip)
                    upstream_network = ipaddress.ip_network(xff_upstream_ip, strict=False)
                    
                    if actual_source_obj in upstream_network:
                        # Request is from trusted upstream, check X-Forwarded-For
                        x_forwarded_for = headers.get('X-Forwarded-For', '').strip()
                        if x_forwarded_for:
                            # X-Forwarded-For can contain multiple IPs, use the first one (original client)
                            client_ip = x_forwarded_for.split(',')[0].strip()
                            logging.debug(f"Request from trusted upstream {actual_source_ip}, using X-Forwarded-For: {client_ip}")
                        else:
                            # From trusted upstream but no X-Forwarded-For header - fail
                            logging.debug(f"Request from trusted upstream {actual_source_ip} but no X-Forwarded-For header")
                            return False
                    else:
                        # Not from trusted upstream, use actual connection IP
                        client_ip = actual_source_ip
                        logging.debug(f"Request from {actual_source_ip} (not trusted upstream), checking direct connection")
                except (ValueError, TypeError) as e:
                    logging.warning(f"Error processing upstream IP: {e}")
                    client_ip = actual_source_ip
            else:
                # X-Forwarded-For not configured or not enabled, use direct connection IP
                client_ip = actual_source_ip
            
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
    config: Dict[str, Any] = {}  # Server configuration for X-Forwarded-For handling
    served_once_rules: set = set()  # Track rules that have been served with serve_once=True
    rule_monitor: Optional[RuleFileMonitor] = None  # Monitor for rule file changes
    rules_lock = threading.Lock()  # Lock for thread-safe access to rules and served_once_rules

    def do_GET(self):
        """Handle GET requests."""
        # Get request headers
        headers = {k: v for k, v in self.headers.items()}

        # Get current rules from monitor if available, otherwise use class variable
        # Use lock to ensure thread-safe access
        with self.rules_lock:
            if self.rule_monitor:
                current_rules = self.rule_monitor.get_rules()
            else:
                current_rules = self.rules.copy()
            
            # Find matching rule
            matching_rule = None
            for rule in current_rules:
                # Check if rule has serve_once and has already been served
                if rule.serve_once and rule.rule_id in self.served_once_rules:
                    logging.info(f"Request denied: Rule {rule.rule_id} has already been served once and cannot be served again (serve_once=True)")
                    continue
                
                if rule.validate(self.path, headers, self.client_address, self.config):
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
                with self.rules_lock:
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

        # Set defaults for optional monitoring configuration
        if 'watch_rules' not in config:
            config['watch_rules'] = True  # Enable by default
        if 'watch_interval' not in config:
            config['watch_interval'] = DEFAULT_POLL_INTERVAL
        
        # Set defaults for X-Forwarded-For configuration
        if 'use_xff' not in config:
            config['use_xff'] = False  # Disabled by default for security
        
        # Validate X-Forwarded-For configuration
        if config.get('use_xff', False):
            if 'xff_upstream_ip' not in config or not config['xff_upstream_ip']:
                logging.critical("FATAL: use_xff is enabled but xff_upstream_ip is not set. "
                               "This is a security risk. Please set xff_upstream_ip to the IP address "
                               "of your trusted proxy/load balancer or set use_xff to False.")
                raise ValueError("use_xff is enabled but xff_upstream_ip is not configured")

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
    GachaHandler.config = config  # Pass config for X-Forwarded-For handling

    # Set up rule file monitoring if enabled
    rule_monitor = None
    if config.get('watch_rules', True):
        poll_interval = config.get('watch_interval', DEFAULT_POLL_INTERVAL)
        rule_monitor = RuleFileMonitor(rules_dir, poll_interval)
        
        # Define callback to update handler's rules
        def update_handler_rules(new_rules, changed_files):
            with GachaHandler.rules_lock:
                GachaHandler.rules = new_rules
                # Only clear serve_once tracking for rules that changed
                # changed_files contains the filenames (basenames) of changed rule files
                if changed_files:
                    # Remove serve_once tracking for rules from changed files
                    rules_to_clear = {rule_id for rule_id in GachaHandler.served_once_rules 
                                     if rule_id in changed_files}
                    for rule_id in rules_to_clear:
                        GachaHandler.served_once_rules.discard(rule_id)
                    if rules_to_clear:
                        logging.info(f"Cleared serve_once tracking for changed rules: {', '.join(rules_to_clear)}")
            logging.info("Handler rules updated")
        
        rule_monitor.start(rules, update_handler_rules)
        GachaHandler.rule_monitor = rule_monitor
        logging.info(f"Rule file monitoring enabled (interval: {poll_interval}s)")
    else:
        logging.info("Rule file monitoring disabled")

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
        if rule_monitor:
            rule_monitor.stop()
        httpd.shutdown()
        sys.exit(0)


if __name__ == '__main__':
    main()
