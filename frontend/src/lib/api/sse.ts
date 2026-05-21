/**
 * SSE subscription utility for realtime task progress.
 *
 * Usage:
 *   const cleanup = subscribe(['generation:42', 'dashboard'], (e) => {
 *     console.log(e.type, e.data);
 *   });
 *   // later…
 *   cleanup();
 */

export interface SseEvent {
	type: string;
	data: Record<string, unknown>;
}

const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;

/**
 * Subscribe to one or more SSE channels.
 *
 * @param channels  Channel strings, e.g. `['generation:1', 'analysis:2']`
 * @param onEvent   Callback invoked for each server-sent event
 * @returns         A cleanup function that closes the connection
 */
export function subscribe(
	channels: string[],
	onEvent: (e: SseEvent) => void
): () => void {
	let es: EventSource | null = null;
	let closed = false;
	let backoff = BASE_BACKOFF_MS;
	let retryTimer: ReturnType<typeof setTimeout> | null = null;

	function connect() {
		if (closed) return;

		const url = `/api/realtime/stream?channels=${encodeURIComponent(channels.join(','))}`;
		es = new EventSource(url, { withCredentials: true });

		es.addEventListener('message', (ev) => {
			try {
				const data = JSON.parse(ev.data) as Record<string, unknown>;
				onEvent({ type: (data['type'] as string) ?? 'update', data });
				backoff = BASE_BACKOFF_MS; // reset on success
			} catch {
				// ignore malformed message
			}
		});

		// Named event types (e.g. "event: status")
		['status', 'progress', 'result', 'update'].forEach((eventType) => {
			es!.addEventListener(eventType, (ev: MessageEvent) => {
				try {
					const data = JSON.parse(ev.data) as Record<string, unknown>;
					onEvent({ type: eventType, data });
					backoff = BASE_BACKOFF_MS;
				} catch {
					// ignore
				}
			});
		});

		es.onerror = () => {
			es?.close();
			es = null;
			if (!closed) {
				// Exponential backoff reconnect
				retryTimer = setTimeout(() => {
					backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
					connect();
				}, backoff);
			}
		};
	}

	connect();

	return () => {
		closed = true;
		if (retryTimer !== null) {
			clearTimeout(retryTimer);
			retryTimer = null;
		}
		es?.close();
		es = null;
	};
}
