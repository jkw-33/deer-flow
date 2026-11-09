import { redirect } from "next/navigation";

export default function LegacyDiagnosesPage() {
  redirect("/diagnoses");
}
