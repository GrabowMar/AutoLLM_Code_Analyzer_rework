export function saveSession(jwt: string) {
  localStorage.setItem("auth_token", jwt);
}
