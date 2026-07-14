# Stage 7 QCIS Compiler Freeze Test Vectors

## 1. Status and encoding

These frozen vectors are part of the `qcis_stage7_calibration_v1` Stage 7.0 implementation authority. The
companion Stage 7 QCIS design-freeze record binds this file and its independent review.

Every source block below is UTF-8 without BOM and has one final LF. Reported byte counts include that LF.
Waveform hashes cover raw contiguous little-endian bytes only: complex arrays use interleaved `<c16` and real
arrays use `<f8`. No JSON, shape, or unit metadata is included in an array hash; those fields are compared
separately.

## 2. Vector `qcis_append_gaussian_v1`

Canonical template, 50 bytes, SHA-256
`6F19B9452256E0CB6A90C80552B54706A60604753D54F77C3A44EF580538CAAD`:

```qcis
PLSXY Q1 1 -1 1 $amplitude 5 0 0 1
I Q1 2
B Q1 Q2
```

The exact binding schema contains only:

```text
amplitude = literal 0.125 GHz
occurrences = 1
allowed position = line 1, PLSXY amplitude
```

Materialized source, 45 bytes, SHA-256
`0153B96563B013E82060A1BC816539E288C84FCC1C6A7C4ABDD4BA01635ECD2D`:

```qcis
PLSXY Q1 1 -1 1 0.125 5 0 0 1
I Q1 2
B Q1 Q2
```

The normalized AST is an ordered three-row sequence:

```text
0 PLSXY target=Q1 waveIndex=1 tStart=-1 length=1 amplitude=0.125
          frequency=5 phase=0 dragAlpha=0 r_sigma=1
1 I     targets=[Q1] length=2
2 B     targets=[Q1,Q2]
```

Its canonical JSON is 289 bytes including final LF, SHA-256
`BB4C52C8CB08DF473F229A6DE3E58056643BFC0AD0FF9D54FF2A32108813F38B`:

```json
{"instructions":[{"amplitude":0.125,"drag_alpha":0.0,"frequency":5.0,"index":0,"length":1,"op":"PLSXY","phase":0.0,"r_sigma":1.0,"t_start":-1,"target":"Q1","wave_index":1},{"index":1,"length":2,"op":"I","targets":["Q1"]},{"index":2,"op":"B","targets":["Q1","Q2"]}],"schema_version":"0.1"}
```

Cursor/frame oracle:

```text
initial                   Q1.xy=0 Q1.z=0 Q2.xy=0 Q2.z=0 frame.Q1=0
after PLSXY [0,1)         Q1.xy=1 Q1.z=0
after I [1,3)             Q1.xy=3 Q1.z=3
after B at max=3          Q1.xy=3 Q1.z=3 Q2.xy=3 Q2.z=3
final sample count        3
```

The canonical trace JSON is 267 bytes including final LF, SHA-256
`7E2B1A023C4EE3765066E1851CB83A1EF943065C99B6EDB1CA98EA8711A6C2F6`:

```json
{"final_cursors":{"Q1":{"xy":3,"z":3},"Q2":{"xy":3,"z":3}},"final_frames":{"Q1":0.0,"Q2":0.0},"final_sample_count":3,"schema_version":"0.1","steps":[{"index":0,"interval":[0,1],"op":"PLSXY"},{"index":1,"interval":[1,3],"op":"I"},{"index":2,"max_cursor":3,"op":"B"}]}
```

Logical-array oracle under default idle flux `{q1=0.1,q2=0,c=0.27}`:

| Array | dtype | shape | Values | SHA-256 |
| --- | --- | --- | --- | --- |
| `q1_xy` | `<c16` | `[3]` | `[0.125+0i,0+0i,0+0i]` | `872379342B861D074CDB5AC2585B3065CF7DBEF86A46E6D2DF8174A35C82F411` |
| `q2_xy` | `<c16` | `[3]` | all zero | `17B0761F87B081D5CF10757CCC89F12BE355C70E2E29DF288B65B30710DCBCD1` |
| `q1_flux` | `<f8` | `[3]` | `[0.1,0.1,0.1]` | `0351A20942B1EEF0AD03C32E1291606671426A06F29CCD3BE1118F5136DA2824` |
| `q2_flux` | `<f8` | `[3]` | `[0,0,0]` | `9D908ECFB6B256DEF8B49A7C504E6C889C4B0E41FE6CE3E01863DD7B61A20AA0` |
| `c_flux` | `<f8` | `[3]` | `[0.27,0.27,0.27]` | `D58A8EFE0795E936484C6CEA8BDC4125802A01155516B3627146628F4A2D95B9` |

This length-one Gaussian deliberately has `center=0`, `g[0]=1`, and `dg[0]=0`, so it tests the exact parser,
binding, sample layout, append, I, and B contracts without a platform-dependent transcendental approximation.

## 3. Vector `qcis_absolute_rectangle_v1`

Concrete source, 39 bytes, SHA-256
`F04AAEC2AA4C79DFE56949148D1952781F483487E57FC5AD441314F9455DD197`:

```qcis
PLS C 0 2 2 0.25 0 0 0 2
I Q1 4
B Q1 C
```

Expected trace:

```text
PLS C occupies absolute [2,4); C.z becomes 4
I Q1 occupies [0,4); Q1.xy and Q1.z become 4
B Q1 C aligns both at 4
final sample count = 4
```

The canonical AST is 236 bytes including final LF, SHA-256
`7CF8365D6D05E83B593C8030F8AB3E1B5CA1EFB6CA465E1807249F2F918BBD2F`:

```json
{"instructions":[{"index":0,"length":2,"op":"PLS","t_start":2,"target":"C","target_flux":0.25,"wave_index":0,"width":2},{"index":1,"length":4,"op":"I","targets":["Q1"]},{"index":2,"op":"B","targets":["Q1","C"]}],"schema_version":"0.1"}
```

The canonical trace is 248 bytes including final LF, SHA-256
`7CD01239D5FF04FA6C2139366DD8098338F5094C3358E53E009B3C9087E6FCA4`:

```json
{"final_cursors":{"C":{"z":4},"Q1":{"xy":4,"z":4}},"final_frames":{"Q1":0.0},"final_sample_count":4,"schema_version":"0.1","steps":[{"index":0,"interval":[2,4],"op":"PLS"},{"index":1,"interval":[0,4],"op":"I"},{"index":2,"max_cursor":4,"op":"B"}]}
```

All affected logical arrays are:

| Array | dtype | shape | Values | SHA-256 |
| --- | --- | --- | --- | --- |
| `q1_xy` | `<c16` | `[4]` | all zero | `F5A5FD42D16A20302798EF6ED309979B43003D2320D9F0E8EA9831A92759FB4B` |
| `q2_xy` | `<c16` | `[4]` | all zero | `F5A5FD42D16A20302798EF6ED309979B43003D2320D9F0E8EA9831A92759FB4B` |
| `q1_flux` | `<f8` | `[4]` | `[0.1,0.1,0.1,0.1]` | `B72C269B78507D682FA8237B148E0FA6B7FD2A14DCE6AF3A76DE9D937D34CF27` |
| `q2_flux` | `<f8` | `[4]` | all zero | `66687AADF862BD776C8FC18B8E9F8E20089714856EE233B3902A591D0D5F2925` |
| `c_flux` | `<f8` | `[4]` | `[0.27,0.27,0.25,0.25]` | `C6804A75C4A7C9F0E17C3986076F5561DC881E7133FE96614097C621F7F8B313` |

## 4. Vector `qcis_nonzero_drag_rz_v1`

Concrete source, 43 bytes, SHA-256
`6F8B72C7B3B16A4267A5515775A3B73A0C0386DC0239940A91BBBB68781C11B3`:

```qcis
RZ Q1 0.25
PLSXY Q1 1 -1 3 0.125 5 0 0.5 1
```

The canonical AST is 246 bytes including final LF, SHA-256
`07FD9431368D572943A616C11D12150D5FAAB6ABF6FC0637471F9CF9DFF4F776`:

```json
{"instructions":[{"index":0,"op":"RZ","phase":0.25,"target":"Q1"},{"amplitude":0.125,"drag_alpha":0.5,"frequency":5.0,"index":1,"length":3,"op":"PLSXY","phase":0.0,"r_sigma":1.0,"t_start":-1,"target":"Q1","wave_index":1}],"schema_version":"0.1"}
```

The canonical trace is 206 bytes including final LF, SHA-256
`4FE82B9B0AE2B0CE901CF98DEF7A301C3C7BEE6AB4D60E384FDAC3E664524331`:

```json
{"final_cursors":{"Q1":{"xy":3,"z":0}},"final_frames":{"Q1":0.25},"final_sample_count":3,"schema_version":"0.1","steps":[{"frame_after":0.25,"index":0,"op":"RZ"},{"index":1,"interval":[0,3],"op":"PLSXY"}]}
```

The `q1_xy` `<c16[3]>` sample components are frozen by binary64 hex, in real/imaginary order:

```text
0  0x1.06798aecdc059p-4  -0x1.c68c93959274ap-5
1  0x1.f01549f7deea1p-4  -0x1.faaeed4f31577p-6
2  0x1.534df4c9f1cb9p-4   0x1.2675d84276b91p-6
```

The raw interleaved `<c16` array is 48 bytes with SHA-256
`156F049E5AD848EBB45B3B1FF48DB245A55014F4D777F8F69ABAC9447F76DB12`. This vector freezes nonzero Gaussian
derivative, `dragAlpha=0.5 samples`, accumulated RZ phase `0.25 rad`, and the single negative QCIS phase sign.

The source `length=3` is the registered Rabi policy literal, not a binding. Remaining logical arrays reuse the
three-sample baseline oracles from `qcis_append_gaussian_v1`: `q2_xy` SHA
`17B0761F87B081D5CF10757CCC89F12BE355C70E2E29DF288B65B30710DCBCD1`, `q1_flux` SHA
`0351A20942B1EEF0AD03C32E1291606671426A06F29CCD3BE1118F5136DA2824`, `q2_flux` SHA
`9D908ECFB6B256DEF8B49A7C504E6C889C4B0E41FE6CE3E01863DD7B61A20AA0`, and `c_flux` SHA
`D58A8EFE0795E936484C6CEA8BDC4125802A01155516B3627146628F4A2D95B9`.

The canonical carrier metadata is 11 bytes including final LF, SHA-256
`2980D038907D648B022B38A95E36A1EA94387243D587006EFCD40D18008100F6`:

```json
{"Q1":5.0}
```

For the compiler-only identity-electronics fixture, effective epsilon arrays must equal the logical arrays
byte-for-byte. The canonical Stage 5.1 coefficient inventory is 380 bytes including final LF, SHA-256
`51A4A0561BACFF0465ABC6B41BEAAF0E408D098E57079FF80F1199C3A5B87AD9`:

```json
{"angular_conversion":"2*pi*GHz_to_rad_per_ns_once","carrier_metadata_sha256":"2980D038907D648B022B38A95E36A1EA94387243D587006EFCD40D18008100F6","dt_ns":0.5,"epsilon_q1_c16_sha256":"156F049E5AD848EBB45B3B1FF48DB245A55014F4D777F8F69ABAC9447F76DB12","epsilon_q2_c16_sha256":"17B0761F87B081D5CF10757CCC89F12BE355C70E2E29DF288B65B30710DCBCD1","sample_count":3,"schema_version":"0.1"}
```

This inventory proves that physical carrier `5.0 GHz` is separately bound once, the RWA epsilon bytes are not
remixed or regenerated, and the sole angular conversion is declared once. The Stage 5.1 two-level state oracle
must additionally compare the final ket against direct `RZ(0.25)=exp(-i*0.25*Z/2)` plus driven evolution under
the frozen solver tolerance; it cannot replace any byte hash above.

The reported nonzero samples were generated with CPython 3.12.10 scalar binary64 `exp/cos/sin` evaluation in
the formula order frozen by the QCIS design. The hex components and raw SHA above, not a tolerance comparison,
are the within-environment oracle. A different platform kernel must still reproduce these bytes or remain
outside this profile's deterministic compiler authority.

## 5. Vector `qcis_calibrated_xy2_macros_v1`

The fixture accepted-simulation calibration binds Q1 to formula `qcis_gaussian_drag_samples_v1`, carrier
`5.0 GHz`, amplitude `0.125 GHz`, length `1 sample`, `r_sigma=1 sample`, and `dragAlpha=0 samples`.
Its canonical JSON is 260 bytes including final LF, SHA-256
`379A9E0871F785804A75AE58301865575E54911C614F877A6401ED6EF2FF383F`:

```json
{"calibration_id":"fixture_q1_xy2_v1","q1":{"amplitude_GHz":0.125,"carrier_frequency_GHz":5.0,"dragAlpha_samples":0.0,"formula_id":"qcis_gaussian_drag_samples_v1","length_samples":1,"r_sigma_samples":1.0},"schema_version":"0.1","status":"accepted_simulation"}
```

The remaining canonical authority fixtures are:

```text
instruction profile  91 bytes  B1EC7717E1C48F4C3009E62FC85974708F860167D3F61602894B6F6AB104E684
QAgent registry      89 bytes  DB283880459C576B24F3DEC5CBB582D6C647B55969AE15D01980B3842A40562C
gate configuration   73 bytes  94758421E453C4DECE1D67594E909496BFD07ED21F9FD2716A8722B26CA1AE80
waveform registry    105 bytes  BC315C2FA78AE5B36923E88B0AF491691F78D756913927B3B6D328E290F1F5E8
clock profile         65 bytes  703353E0767FD2A5945E524E7AA57493EA240399F15A516234C7ACA4AD126647
compiler snapshot    225 bytes  ABCBEAB8CB41B32D39E142F44C121F6FEF6A4782A61C4AFB79670F04A3F85774
```

Each JSON block includes one final LF:

```json
{"profile_id":"qcis_stage7_calibration_v1","profile_version":"0.1","schema_version":"0.1"}
```

```json
{"Q1":{"component":"q1","xy_channel":"q1_xy","z_channel":"q1_z"},"schema_version":"0.1"}
```

```json
{"Q1":{"active_xy2_setting":"fixture_q1_xy2_v1"},"schema_version":"0.1"}
```

```json
{"schema_version":"0.1","settings":{"fixture_q1_xy2_v1":{"formula_id":"qcis_gaussian_drag_samples_v1"}}}
```

```json
{"dt_ns":0.5,"sample_rate_Hz":2000000000,"schema_version":"0.1"}
```

```json
{"compiler_id":"sqvm_qcis_compiler_v1","compiler_version":"0.1","numeric_kernel":"cpython_3.12.10_scalar_binary64_v1","schema_version":"0.1","source_sha256":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
```

The compiler source hash is a synthetic fixture value, not a production-source claim. A real run substitutes
the verified compiler source snapshot while retaining the same exact schema and provenance edge.

Concrete source, 14 bytes, SHA-256
`73516313AEBCE269D4ECC8146C6053570359A6DD8C80A96158A4B5CE4996E6B4`:

```qcis
X2P Q1
Y2P Q1
```

The source is registered by this 284-byte canonical program envelope, SHA-256
`D0FF020540D28105E2783AFB1E4780C19C22984C052DEC233FDFD58A17757D2A`:

```json
{"bindings":{},"instruction_set_id":"qcis_stage7_calibration_v1","program_schema_version":"0.1","source":"X2P Q1\nY2P Q1\n","source_format":"qcis_template","template_id":"qcis_xy2_macro_fixture_v1","template_sha256":"73516313AEBCE269D4ECC8146C6053570359A6DD8C80A96158A4B5CE4996E6B4"}
```

The canonical AST is 116 bytes including final LF, SHA-256
`BBFB1D2399D20F725BBDE29E657C14582EA701B99A2B36DC431EB038EC53A9CA`:

```json
{"instructions":[{"index":0,"op":"X2P","target":"Q1"},{"index":1,"op":"Y2P","target":"Q1"}],"schema_version":"0.1"}
```

The canonical expansion/provenance trace is 1170 bytes including final LF, SHA-256
`E6C4457593D6D6942439A53DFF7BC12326425F020F64AA35DE0BF65AEA68831E`:

```json
{"authority_sha256":{"calibration":"379A9E0871F785804A75AE58301865575E54911C614F877A6401ED6EF2FF383F","clock":"703353E0767FD2A5945E524E7AA57493EA240399F15A516234C7ACA4AD126647","compiler":"ABCBEAB8CB41B32D39E142F44C121F6FEF6A4782A61C4AFB79670F04A3F85774","gate_configuration":"94758421E453C4DECE1D67594E909496BFD07ED21F9FD2716A8722B26CA1AE80","instruction_profile":"B1EC7717E1C48F4C3009E62FC85974708F860167D3F61602894B6F6AB104E684","program":"D0FF020540D28105E2783AFB1E4780C19C22984C052DEC233FDFD58A17757D2A","qagent_registry":"DB283880459C576B24F3DEC5CBB582D6C647B55969AE15D01980B3842A40562C","waveform_registry":"BC315C2FA78AE5B36923E88B0AF491691F78D756913927B3B6D328E290F1F5E8"},"final_cursors":{"Q1":{"xy":2,"z":0}},"final_sample_count":2,"schema_version":"0.1","steps":[{"calibration_keys":["amplitude_GHz","carrier_frequency_GHz","dragAlpha_samples","formula_id","length_samples","r_sigma_samples"],"emitted_interval":[0,1],"index":0,"op":"X2P","phase":0.0},{"calibration_keys":["amplitude_GHz","carrier_frequency_GHz","dragAlpha_samples","formula_id","length_samples","r_sigma_samples"],"emitted_interval":[1,2],"index":1,"op":"Y2P","phase":1.5707963267948966}]}
```

Expansion emits two consecutive one-sample Gaussian logical pulses. The profile constant pi is binary64
`0x1.921fb54442d18p+1`; X2P uses gate phase zero and Y2P uses `pi/2` inside the single negative QCIS phase.
The `q1_xy` `<c16[2]>` components are:

```text
0  0x1.0000000000000p-3   0x0.0p+0
1  0x1.1a62633145c07p-57 -0x1.0000000000000p-3
```

The raw array is 32 bytes, SHA-256
`AD92ABD3DBECF6F2B61E87834632713F9D97DA6A14AC3E6F1DB5A9739E89DD7E`. The final Q1 XY cursor is 2 and
the expansion trace must bind every consumed calibration key plus formula/profile/config hashes.

The single-line source `X2P Q1\n` is 7 bytes, SHA-256
`969D5B24ADC265087BBFF45F2D99AA4DE4C09AC3C7E044A62676E1B5C35A1F4F`. With any one prerequisite key absent,
it rejects pre-reservation as `QCIS_MACRO_CALIBRATION_INCOMPLETE`.

## 6. Stable rejection vectors

Concrete numeric waveform, 16 bytes, SHA-256
`6AC39D0B30E57F2449B898FCF6000AE57A149CE39A08D1D173906ED500B23926`:

```qcis
PLS Q1 -1 0 0 1
```

Expected pre-reservation reason: `QCIS_UNSUPPORTED_NUMERIC_WAVEFORM`.

Reserved flattop wave index, 26 bytes, SHA-256
`8F221C75A22C279995C126643A1B2323E27EBC264318A43BE3E52D51A964E8AA`:

```qcis
PLS C 2 -1 4 0.25 0 0 0 1
```

Expected pre-reservation reason: `QCIS_UNSUPPORTED_WAVE_INDEX`.

Additional exact reason codes required by this profile are:

```text
QCIS_NONCANONICAL_SOURCE
QCIS_NONCANONICAL_NUMBER
QCIS_UNKNOWN_OPERATION
QCIS_UNKNOWN_QAGENT
QCIS_ARITY_MISMATCH
QCIS_TEMPLATE_ID_MISMATCH
QCIS_TEMPLATE_SHA_MISMATCH
QCIS_BINDING_SET_MISMATCH
QCIS_BINDING_POSITION_FORBIDDEN
QCIS_BINDING_UNIT_MISMATCH
QCIS_TIMING_OVERLAP
QCIS_TIMING_OUT_OF_BUDGET
QCIS_CARRIER_CHANGE_UNSUPPORTED
QCIS_MACRO_CALIBRATION_INCOMPLETE
QCIS_CALIBRATION_AUTHORITY_HASH_MISMATCH
QCIS_PROFILE_AUTHORITY_HASH_MISMATCH
QCIS_QAGENT_AUTHORITY_HASH_MISMATCH
QCIS_GATE_CONFIG_AUTHORITY_HASH_MISMATCH
QCIS_WAVEFORM_REGISTRY_AUTHORITY_HASH_MISMATCH
QCIS_CLOCK_AUTHORITY_HASH_MISMATCH
QCIS_COMPILER_AUTHORITY_HASH_MISMATCH
QCIS_MEASUREMENT_STAGE8_REQUIRED
QCIS_LOGICAL_WAVEFORM_HASH_MISMATCH
STAGE4_1_EFFECTIVE_CONTROL_HASH_MISMATCH
STAGE5_1_COEFFICIENT_INVENTORY_MISMATCH
```

For the tamper oracle, flip bit 0 of byte 0 in `qcis_nonzero_drag_rz_v1`'s 48-byte `q1_xy` array. The corrupted
SHA-256 is `22C2412EABB5A1D3FD920AE3BE76521AEE487DD448140BE8CE230DA029C8F84F` and verification must reject it as
`QCIS_LOGICAL_WAVEFORM_HASH_MISMATCH` before Stage 4.1. Under the identity-electronics fixture, the same exact
48-byte mutation after Stage 4.1 publication has the same corrupted SHA and rejects as
`STAGE4_1_EFFECTIVE_CONTROL_HASH_MISMATCH`. Flip bit 0 of byte 0 in the 380-byte canonical coefficient inventory;
its corrupted SHA is `1C828BB55DBA298653D18B893405499836CD00B46903A544E58F76877AB38613`, and it rejects as
`STAGE5_1_COEFFICIENT_INVENTORY_MISMATCH`. No backend or result is produced.

Decimal-token boundary table:

| Token | Context | Result |
| --- | --- | --- |
| `0`, `1`, `0.0`, `-0.25`, `1e3`, `1E-3` | registered real operand | accept |
| `-1` | fixed `tStart` only | accept |
| `+1`, `01`, `.5`, `1.`, `1e+3`, `-0`, `-0.0` | any numeric operand | `QCIS_NONCANONICAL_NUMBER` |
| `NaN`, `Inf`, `0x1p0`, `1,5`, `pi` | any numeric operand | `QCIS_NONCANONICAL_NUMBER` |

Template-tamper source replacing the registered `$amplitude` with `$frequency` is 50 bytes, SHA-256
`74EBE3922706EB37648CF933855EEAE9A29A24B6A940FA1F93C5D5256A8AF36B`:

```qcis
PLSXY Q1 1 -1 1 $frequency 5 0 0 1
I Q1 2
B Q1 Q2
```

Even if a binding named `frequency` exists elsewhere, this source rejects as `QCIS_TEMPLATE_SHA_MISMATCH` before
binding resolution. Supplying the original source with a changed binding key set rejects as
`QCIS_BINDING_SET_MISMATCH`.

For calibration/provenance tampering, flip bit 0 of byte 0 in the 260-byte macro calibration fixture. Its
corrupted SHA-256 is `A41715A7DDDCF490A37D1AD35EE30657ACB5B87B038212F55092BC024C55F6CA`, and expansion rejects as
`QCIS_CALIBRATION_AUTHORITY_HASH_MISMATCH`. The same single-bit mutation against any profile/config/registry
fixture rejects before expansion with its corresponding authority-hash reason. All tamper cases assert zero
reservation, output, backend dispatch, and catalog side effects.

Every reject vector produces no reservation, run directory, catalog row, or output payload.

## 7. Freeze completion rule

The implementation and independent-test repositories must materialize these same source bytes and independently
recompute every reported SHA. Nonzero transcendental vectors bind the exact numerical
kernel and environment lock used by the frozen compiler; tolerance-only comparisons cannot replace raw logical
waveform hashes within one bound environment.
