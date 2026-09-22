// Joriy foydalanuvchi roli — /api/me javobidan, ilova chizilishidan oldin o'rnatiladi.
// Server baribir tekshiradi (403); bu faqat keraksiz tugmalarni ko'rsatmaslik uchun.
export type Role = "admin" | "viewer";

export let ROLE: Role = "viewer";
export function setRole(r: string | undefined) {
  ROLE = r === "admin" ? "admin" : "viewer";
}

export const canEdit = () => ROLE === "admin";

export const ROLE_LABEL: Record<Role, string> = { admin: "Administrator", viewer: "Kuzatuvchi" };
