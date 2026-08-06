"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

/**
 * O estúdio virou aba da persona.
 *
 * Esta rota fica como redirecionamento porque links antigos existem —
 * `/estudio?persona=<id>` era o que a tela gerava a cada troca de personagem,
 * então está no histórico e nos favoritos de quem usou. Quebrar isso com 404
 * não ensina para onde foi.
 */
export default function Estudio() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">redirecionando…</p>}>
      <Redireciona />
    </Suspense>
  );
}

function Redireciona() {
  const router = useRouter();
  const persona = useSearchParams().get("persona");

  useEffect(() => {
    router.replace(persona ? `/personas/${persona}` : "/personas");
  }, [persona, router]);

  return (
    <p className="text-sm text-slate-500">
      O estúdio agora é uma aba dentro de cada persona. Levando você para lá…
    </p>
  );
}
