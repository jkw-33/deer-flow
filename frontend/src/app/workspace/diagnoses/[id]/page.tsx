import { redirect } from "next/navigation";

export default async function LegacyDiagnosisPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/diagnoses/${encodeURIComponent(id)}`);
}
