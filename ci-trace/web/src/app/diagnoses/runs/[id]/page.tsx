import { notFound } from "next/navigation";

import { LiveDiagnosis } from "./live-diagnosis";

export default async function LiveDiagnosisPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id)) notFound();
  return <LiveDiagnosis id={id} />;
}
