"use client";

import { useState, use } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ArrowLeft, Smartphone, Monitor, Camera, AlertTriangle, ShieldAlert,
  Grid3x3, Video, VideoOff,
} from "lucide-react";
import { api } from "@/lib/api";
import { urlDaTela, tempoRelativo } from "@/lib/servers";

/**
 * Devices de um servidor.
 *
 * É aqui que o monitoramento de tela vai morar. A base já está montada —
 * device com endereço ADB e dispositivo de vídeo, gateway cadastrado como
 * serviço — e a interface já mostra quem está apto e quem não está. O que
 * falta é o gateway existir do lado de lá.
 */
export default function DevicesDoServidor({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data } = useSWR(`/v1/servers/${id}/devices`, api, { refreshInterval: 20_000 });
  const { data: lista } = useSWR("/v1/servers", api);

  const [grade, setGrade] = useState(false);

  const devices = data?.devices ?? [];
  const gateway = data?.screen_gateway;
  const servidor = (lista?.servers ?? []).find((s: any) => s.id === id);

  const aptos = devices.filter((d: any) => d.screencast_capable && d.alive);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <Link href="/servidores" className="flex w-fit items-center gap-1 text-xs text-slate-500 hover:text-slate-300">
          <ArrowLeft size={12} /> servidores
        </Link>
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-slate-100">
            {servidor?.name ?? "Servidor"}
            <span className="ml-2 text-sm font-normal text-slate-500">
              {devices.length} device{devices.length === 1 ? "" : "s"}
            </span>
          </h1>

          {/* Grade ao vivo — pronta, aguardando o gateway existir. */}
          <button
            onClick={() => setGrade((v) => !v)}
            disabled={!gateway || aptos.length === 0}
            title={
              !gateway
                ? "Nenhum gateway de tela cadastrado neste servidor"
                : aptos.length === 0
                  ? "Nenhum device apto à transmissão contínua"
                  : "Ver todas as telas ao vivo"
            }
            className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Grid3x3 size={14} /> grade ao vivo
          </button>
        </div>
      </header>

      <AvisoDeTela gateway={gateway} aptos={aptos.length} total={devices.length} />

      {grade && gateway && <GradeAoVivo devices={aptos} gateway={gateway} />}

      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {devices.map((d: any) => (
          <CardDevice key={d.id} device={d} gateway={gateway} />
        ))}
      </div>

      {devices.length === 0 && (
        <div className="card text-center text-sm text-slate-500">
          Nenhum device. Sondar o servidor preenche esta lista a partir do
          inventário dos serviços.
        </div>
      )}
    </div>
  );
}

function AvisoDeTela({ gateway, aptos, total }: { gateway: any; aptos: number; total: number }) {
  if (!gateway) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-surface-border bg-surface-raised/60 p-3 text-sm">
        <VideoOff size={16} className="mt-0.5 shrink-0 text-slate-600" />
        <div className="text-slate-500">
          <strong className="text-slate-400">Transmissão de tela não configurada.</strong>{" "}
          Cadastre um serviço do tipo <em>Gateway de tela</em> neste servidor para
          assistir aos devices daqui. A interface já está pronta; falta o endereço.
        </div>
      </div>
    );
  }
  if (aptos === 0 && total > 0) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-amber-200/90">
        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
        <div>
          Gateway cadastrado, mas nenhum device está apto. A transmissão contínua
          exige que o container exponha um dispositivo de vídeo — sem ele, só
          existe o modo que derruba quem já estava assistindo, inútil para uma grade.
        </div>
      </div>
    );
  }
  return null;
}

function GradeAoVivo({ devices, gateway }: { devices: any[]; gateway: any }) {
  // Cada tela aberta é um processo de codificação no host. Um teto explícito
  // evita derrubar o servidor sem perceber.
  const TETO = 6;
  const visiveis = devices.slice(0, TETO);

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <span className="label">Telas ao vivo</span>
        <span className="text-xs text-slate-500">
          {visiveis.length} de {devices.length}
          {devices.length > TETO && ` · teto de ${TETO} por vez`}
        </span>
      </div>

      <p className="text-xs text-slate-600">
        Somente visualização: o Olimpo nunca assume o controle de um device que
        um curador esteja operando.
      </p>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {visiveis.map((d) => {
          const url = urlDaTela(gateway.web_url || gateway.base_url, d);
          return (
            <div key={d.id} className="space-y-1">
              <div className="truncate font-mono text-xs text-slate-500">{d.container}</div>
              {url ? (
                <iframe
                  src={url}
                  allow="autoplay"
                  loading="lazy"
                  className="aspect-[9/19] w-full rounded border border-surface-border bg-black"
                />
              ) : (
                <div className="flex aspect-[9/19] w-full items-center justify-center rounded border border-surface-border bg-surface text-xs text-slate-600">
                  sem vídeo
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CardDevice({ device, gateway }: { device: any; gateway: any }) {
  const [tela, setTela] = useState(false);
  const contas = device.accounts ?? [];
  const url = gateway ? urlDaTela(gateway.web_url || gateway.base_url, device) : null;

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <Smartphone size={14} className="shrink-0 text-slate-500" />
            <span className="truncate font-mono text-sm text-slate-200">
              {device.container}
            </span>
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-slate-600">
            {device.android_version && <span>Android {device.android_version}</span>}
            {device.adb_target && <span className="font-mono">{device.adb_target}</span>}
            {device.video_device && <span className="font-mono">{device.video_device}</span>}
          </div>
        </div>
        <span className={`shrink-0 text-xs ${device.alive ? "text-active" : "text-block"}`}>
          ● {device.alive ? "vivo" : "parado"}
        </span>
      </div>

      {/* Dois sistemas agindo no mesmo device é como uma publicação é
          interrompida no meio — e uma conta é queimada. */}
      {device.under_external_management && (
        <div className="flex items-center gap-1.5 rounded border border-warn/30 bg-warn/5 px-2 py-1 text-xs text-amber-200">
          <ShieldAlert size={12} className="shrink-0" />
          sob vigilância automática do android-manager
        </div>
      )}

      <div className="space-y-1 border-t border-surface-border pt-2">
        {contas.length === 0 ? (
          <span className="text-xs text-slate-600">nenhuma conta logada</span>
        ) : (
          contas.map((c: any, i: number) => (
            <div key={i} className="flex items-center gap-1.5 text-xs">
              <span className={c.foreground ? "text-active" : "text-slate-700"}>●</span>
              <span className="truncate text-slate-400">{c.handle}</span>
              <span className="text-slate-600">{c.platform}</span>
              {c.android_user != null && (
                <span className="font-mono text-slate-700">user {c.android_user}</span>
              )}
              {/* O serviço reporta "usuário em execução", não quem está na
                  tela: o Android mantém vários usuários rodando ao mesmo tempo. */}
              {c.foreground && <span className="text-active">usuário ativo</span>}
            </div>
          ))
        )}
      </div>

      <div className="flex items-center justify-between border-t border-surface-border pt-2">
        <span className="text-[10px] text-slate-600">
          visto {tempoRelativo(device.last_seen_at)}
        </span>
        <div className="flex gap-1.5">
          {/* Captura sob demanda — depende do android-manager cadastrado. */}
          <button
            disabled
            title="Captura de tela: requer o android-manager cadastrado neste servidor"
            className="btn-ghost disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Camera size={12} /> captura
          </button>
          <button
            onClick={() => setTela((v) => !v)}
            disabled={!url}
            title={
              !gateway ? "Nenhum gateway de tela cadastrado"
                : !device.video_device ? "Device sem dispositivo de vídeo"
                  : "Ver a tela ao vivo"
            }
            className="btn-ghost disabled:cursor-not-allowed disabled:opacity-40"
          >
            {tela ? <VideoOff size={12} /> : <Video size={12} />} tela
          </button>
        </div>
      </div>

      {tela && url && (
        <iframe
          src={url}
          allow="autoplay"
          className="aspect-[9/19] w-full rounded border border-surface-border bg-black"
        />
      )}
    </div>
  );
}
