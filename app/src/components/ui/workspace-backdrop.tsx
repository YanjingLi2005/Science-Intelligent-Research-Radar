/**
 * Quiet scientific backdrop for every authenticated workspace page.
 * The artwork stays at the edges so dense research content remains readable.
 */
export default function WorkspaceBackdrop() {
  return (
    <div className="workspace-backdrop pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden="true">
      <div className="workspace-backdrop__wash absolute inset-0" />
      <div className="workspace-backdrop__grid absolute inset-0" />

      <svg
        className="workspace-backdrop__map absolute inset-0 h-full w-full"
        viewBox="0 0 1600 1000"
        preserveAspectRatio="xMidYMid slice"
        fill="none"
      >
        <g className="workspace-backdrop__orbit">
          <circle cx="1315" cy="178" r="132" />
          <circle cx="1315" cy="178" r="76" strokeDasharray="5 12" />
          <path d="M1140 178h350M1315 3v350" />
          <circle className="workspace-backdrop__node workspace-backdrop__node-strong" cx="1315" cy="178" r="3.5" />
          <circle className="workspace-backdrop__node" cx="1413" cy="90" r="2.5" />
          <circle className="workspace-backdrop__node" cx="1242" cy="113" r="2.5" />
        </g>

        <g className="workspace-backdrop__constellation workspace-backdrop__constellation--left">
          <path d="M64 304 142 245l74 54 91-99 84 73 78-28" />
          <path d="m142 245 31 117 134-162 24 146 138-101" />
          <path d="m64 304 109 58 158-16 60-73" />
          <circle cx="64" cy="304" r="2.4" />
          <circle cx="142" cy="245" r="2.4" />
          <circle cx="173" cy="362" r="2.4" />
          <circle cx="216" cy="299" r="2.4" />
          <circle cx="307" cy="200" r="2.4" />
          <circle cx="331" cy="346" r="2.4" />
          <circle cx="391" cy="273" r="2.4" />
          <circle cx="469" cy="245" r="2.4" />
        </g>

        <g className="workspace-backdrop__constellation workspace-backdrop__constellation--bottom">
          <path d="m1055 866 91-86 81 61 89-124 103 69 113-87" />
          <path d="m1146 780 40 146 130-209 27 157 189-175" />
          <path d="m1055 866 131 60 157-52 76-88 113-87" />
          <circle cx="1055" cy="866" r="2.4" />
          <circle cx="1146" cy="780" r="2.4" />
          <circle cx="1186" cy="926" r="2.4" />
          <circle cx="1227" cy="841" r="2.4" />
          <circle cx="1316" cy="717" r="2.4" />
          <circle cx="1343" cy="874" r="2.4" />
          <circle cx="1419" cy="786" r="2.4" />
          <circle cx="1532" cy="699" r="2.4" />
        </g>

        <g className="workspace-backdrop__ticks">
          <path d="M546 116h42M567 95v42M849 880h42M870 859v42M1464 484h42" />
          <circle cx="760" cy="132" r="2" />
          <circle cx="981" cy="315" r="2" />
          <circle cx="537" cy="786" r="2" />
        </g>
      </svg>

      <div className="workspace-backdrop__vignette absolute inset-0" />
    </div>
  );
}
