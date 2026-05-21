import type { Handle, HandleServerError } from '@sveltejs/kit';

const API_TARGET = process.env.API_TARGET ?? 'http://localhost:8001';

const PROXY_PREFIXES = ['/api/', '/_allauth/', '/admin/', '/media/'];

export const handle: Handle = async ({ event, resolve }) => {
    const path = event.url.pathname;

    if (PROXY_PREFIXES.some((prefix) => path.startsWith(prefix))) {
        const targetUrl = `${API_TARGET}${path}${event.url.search}`;
        const reqBody =
            event.request.method !== 'GET' && event.request.method !== 'HEAD'
                ? await event.request.arrayBuffer()
                : undefined;

        const upstream = await fetch(targetUrl, {
            method: event.request.method,
            headers: event.request.headers,
            body: reqBody,
            // @ts-expect-error Node 18+ fetch supports duplex
            duplex: reqBody ? 'half' : undefined,
        });

        return new Response(upstream.body, {
            status: upstream.status,
            statusText: upstream.statusText,
            headers: upstream.headers,
        });
    }

    return resolve(event);
};

export const handleError: HandleServerError = async ({ error }) => {
    console.error('Server error:', error);
    return {
        message: 'An unexpected error occurred',
    };
};
