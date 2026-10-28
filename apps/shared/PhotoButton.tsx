import { useRef, useState } from "react";
import { Camera } from "@phosphor-icons/react";
import { call } from "./api";

/** Choose or take a catalogue photo for a product. The backend shrinks it and uses it everywhere
 *  the product is shown (catalogue, cart, bill). */
export function PhotoButton({ productId, token, hasPhoto, onDone }: { productId: string; token: string; hasPhoto: boolean; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  function upload(file: File) {
    setBusy(true);
    setError("");
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await call(`/staff/products/${encodeURIComponent(productId)}/thumbnail`, token, { image_base64: String(reader.result) });
        onDone();
      } catch (e: any) {
        setError(String(e.message || e));
      } finally {
        setBusy(false);
        if (input.current) input.current.value = "";
      }
    };
    reader.onerror = () => {
      setError("Couldn't read that file");
      setBusy(false);
    };
    reader.readAsDataURL(file);
  }
  return (
    <span className="photobutton">
      <input ref={input} type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      <button className="secondary" disabled={busy} onClick={() => input.current?.click()}>
        <Camera size={16} /> {busy ? "Uploading…" : hasPhoto ? "Change photo" : "Add photo"}
      </button>
      {error && <small className="error">{error}</small>}
    </span>
  );
}
