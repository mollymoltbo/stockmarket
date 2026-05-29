<?php
/**
 * api.php — central hub for the stock scanner (zone.ee).
 *
 * Replaces the git data-bridge: every party POSTs here (token-gated) and the
 * dashboard / Claude routine reads here. Two data shapes:
 *
 *   feed  — append-only message log (scan events, Claude verdicts, manual notes)
 *   state — last-write-wins named blobs ("scan", "watchlist") for rendering and
 *           for the routine to read each name's trajectory
 *
 * Storage is flat files (no DB extension needed) under a directory OUTSIDE the
 * web root, with locking. Writes require the shared token in an `X-Auth` header;
 * reads are open (the dashboard is public by choice).
 *
 *   POST ?action=message      body: {source,kind,title,body,body_html,meta}
 *   POST ?action=state&name=X body: {data:{...}}   (or a bare object)
 *   GET  ?action=feed&limit=N
 *   GET  ?action=state&name=X
 *   GET  ?action=all          -> {scan, watchlist, feed, generated_at}
 *   GET  ?action=ping
 *
 * Config: copy api_config.php.example -> api_config.php (gitignored) and set
 * $API_TOKEN and $DATA_DIR. Falls back to env SCANNER_API_TOKEN / SCANNER_DATA_DIR.
 */
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');

$API_TOKEN = '';
$DATA_DIR  = __DIR__ . '/../scanner_data';        // default: one level ABOVE web root
if (is_file(__DIR__ . '/api_config.php')) { require __DIR__ . '/api_config.php'; }
if ($API_TOKEN === '') { $API_TOKEN = getenv('SCANNER_API_TOKEN') ?: ''; }
$envDir = getenv('SCANNER_DATA_DIR'); if ($envDir) { $DATA_DIR = $envDir; }
$DATA_DIR = rtrim($DATA_DIR, '/');
if (!is_dir($DATA_DIR)) { @mkdir($DATA_DIR, 0700, true); }

const MAX_MESSAGES = 500;
const MAX_BODY     = 262144;   // 256 KB

function out($data, int $code = 200): void {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}
function msg_file(string $dir): string { return $dir . '/messages.ndjson'; }
function state_file(string $dir, string $name): string {
    return $dir . '/state_' . preg_replace('/[^a-z0-9_]/i', '', $name) . '.json';
}
function read_messages(string $f): array {
    if (!is_file($f)) return [];
    $rows = [];
    foreach (file($f, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
        $d = json_decode($line, true);
        if (is_array($d)) $rows[] = $d;
    }
    return $rows;
}
function write_atomic(string $f, string $contents): void {
    $tmp = $f . '.tmp' . getmypid();
    file_put_contents($tmp, $contents, LOCK_EX);
    rename($tmp, $f);
}

$action = $_GET['action'] ?? '';
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($method === 'GET' && $action === 'ping') out(['ok' => true, 'service' => 'scanner-api']);

if ($method === 'POST') {
    $supplied = $_SERVER['HTTP_X_AUTH'] ?? '';
    if ($API_TOKEN === '' || !hash_equals($API_TOKEN, $supplied)) out(['error' => 'unauthorized'], 401);

    $raw = file_get_contents('php://input', false, null, 0, MAX_BODY + 1);
    if ($raw !== false && strlen($raw) > MAX_BODY) out(['error' => 'payload too large'], 413);
    $in = json_decode($raw ?: 'null', true);
    if (!is_array($in)) out(['error' => 'invalid json body'], 400);

    if ($action === 'message') {
        $m = [
            'ts'        => gmdate('c'),
            'source'    => substr((string)($in['source'] ?? 'unknown'), 0, 40),
            'kind'      => substr((string)($in['kind'] ?? 'note'), 0, 24),
            'title'     => substr((string)($in['title'] ?? ''), 0, 240),
            'body'      => (string)($in['body'] ?? ''),
            'body_html' => (string)($in['body_html'] ?? ''),
            'meta'      => isset($in['meta']) && is_array($in['meta']) ? $in['meta'] : null,
        ];
        $f = msg_file($DATA_DIR);
        $fp = fopen($f, 'c+');
        if (!$fp) out(['error' => 'storage open failed'], 500);
        flock($fp, LOCK_EX);
        fseek($fp, 0, SEEK_END);
        fwrite($fp, json_encode($m, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . "\n");
        flock($fp, LOCK_UN);
        fclose($fp);
        $all = read_messages($f);
        if (count($all) > MAX_MESSAGES) {
            $all = array_slice($all, -MAX_MESSAGES);
            write_atomic($f, implode("\n", array_map(
                fn($x) => json_encode($x, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), $all)) . "\n");
        }
        out(['ok' => true, 'message' => $m]);
    }

    if ($action === 'state') {
        $name = (string)($_GET['name'] ?? '');
        if ($name === '') out(['error' => 'name required'], 400);
        $blob = ['ts' => gmdate('c'), 'data' => $in['data'] ?? $in];
        write_atomic(state_file($DATA_DIR, $name),
            json_encode($blob, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));
        out(['ok' => true, 'name' => $name, 'ts' => $blob['ts']]);
    }

    out(['error' => 'unknown action'], 404);
}

if ($method === 'GET') {
    if ($action === 'feed') {
        $limit = max(1, min(MAX_MESSAGES, (int)($_GET['limit'] ?? 50)));
        out(['feed' => array_reverse(array_slice(read_messages(msg_file($DATA_DIR)), -$limit))]);
    }
    if ($action === 'state') {
        $name = (string)($_GET['name'] ?? '');
        $f = state_file($DATA_DIR, $name);
        out(is_file($f) ? json_decode(file_get_contents($f), true) + ['name' => $name]
                        : ['name' => $name, 'ts' => null, 'data' => null]);
    }
    if ($action === 'all') {
        $unwrap = function (string $name) use ($DATA_DIR) {
            $f = state_file($DATA_DIR, $name);
            return is_file($f) ? (json_decode(file_get_contents($f), true)['data'] ?? null) : null;
        };
        out([
            'generated_at' => gmdate('c'),
            'scan'         => $unwrap('scan'),
            'watchlist'    => $unwrap('watchlist'),
            'feed'         => array_reverse(array_slice(read_messages(msg_file($DATA_DIR)), -50)),
        ]);
    }
    out(['error' => 'unknown action'], 404);
}

out(['error' => 'method not allowed'], 405);
