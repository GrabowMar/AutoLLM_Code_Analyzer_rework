import type { HandleClientError } from '@sveltejs/kit';

export const handleError: HandleClientError = async ({ error, message }) => {
	console.error('Client error:', error);
	return {
		message: message || 'An unexpected error occurred',
	};
};
