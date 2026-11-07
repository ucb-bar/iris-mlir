// Verilated -*- C++ -*-
// DESCRIPTION: Verilator output: Primary model header
//
// This header should be included by all source files instantiating the design.
// The class here is then constructed to instantiate the design.
// See the Verilator manual for examples.

#ifndef VERILATED_VIRISROCKETFRONTENDPEER_H_
#define VERILATED_VIRISROCKETFRONTENDPEER_H_  // guard

#include "verilated.h"

class VIrisRocketFrontendPeer__Syms;
class VIrisRocketFrontendPeer___024root;

// This class is the main interface to the Verilated model
class alignas(VL_CACHE_LINE_BYTES) VIrisRocketFrontendPeer VL_NOT_FINAL : public VerilatedModel {
    friend class VIrisRocketFrontendPeer__Syms;
  private:
    // Symbol table holding complete model state (owned by this class)
    VIrisRocketFrontendPeer__Syms* const vlSymsp;
    // Evaluation loop
    VerilatedEvalLoop m_evalLoop;

  public:

    // CONSTEXPR CAPABILITIES
    // Verilated with --trace?
    static constexpr bool traceCapable = false;

    // PORTS
    // The application code writes and reads these signals to
    // propagate new values into/out from the Verilated model.
    VL_IN8(&clock,0,0);
    VL_IN8(&reset,0,0);
    VL_IN8(&auto_icache_master_out_a_ready,0,0);
    VL_OUT8(&auto_icache_master_out_a_valid,0,0);
    VL_IN8(&auto_icache_master_out_d_valid,0,0);
    VL_IN8(&auto_icache_master_out_d_bits_opcode,2,0);
    VL_IN8(&auto_icache_master_out_d_bits_size,3,0);
    VL_IN8(&auto_icache_master_out_d_bits_corrupt,0,0);
    VL_IN8(&io_cpu_might_request,0,0);
    VL_IN8(&io_cpu_req_valid,0,0);
    VL_IN8(&io_cpu_req_bits_speculative,0,0);
    VL_IN8(&io_cpu_sfence_valid,0,0);
    VL_IN8(&io_cpu_sfence_bits_rs1,0,0);
    VL_IN8(&io_cpu_sfence_bits_rs2,0,0);
    VL_OUT8(&io_cpu_resp_valid,0,0);
    VL_OUT8(&io_cpu_resp_bits_btb_taken,0,0);
    VL_OUT8(&io_cpu_resp_bits_btb_bridx,0,0);
    VL_OUT8(&io_cpu_resp_bits_btb_entry,4,0);
    VL_OUT8(&io_cpu_resp_bits_btb_bht_history,7,0);
    VL_OUT8(&io_cpu_resp_bits_xcpt_pf_inst,0,0);
    VL_OUT8(&io_cpu_resp_bits_xcpt_gf_inst,0,0);
    VL_OUT8(&io_cpu_resp_bits_xcpt_ae_inst,0,0);
    VL_OUT8(&io_cpu_resp_bits_replay,0,0);
    VL_IN8(&io_cpu_btb_update_valid,0,0);
    VL_IN8(&io_cpu_btb_update_bits_prediction_entry,4,0);
    VL_IN8(&io_cpu_btb_update_bits_isValid,0,0);
    VL_IN8(&io_cpu_btb_update_bits_cfiType,1,0);
    VL_IN8(&io_cpu_bht_update_valid,0,0);
    VL_IN8(&io_cpu_bht_update_bits_prediction_history,7,0);
    VL_IN8(&io_cpu_bht_update_bits_branch,0,0);
    VL_IN8(&io_cpu_bht_update_bits_taken,0,0);
    VL_IN8(&io_cpu_bht_update_bits_mispredict,0,0);
    VL_IN8(&io_cpu_flush_icache,0,0);
    VL_IN8(&io_cpu_progress,0,0);
    VL_IN8(&io_ptw_req_ready,0,0);
    VL_OUT8(&io_ptw_req_valid,0,0);
    VL_OUT8(&io_ptw_req_bits_valid,0,0);
    VL_OUT8(&io_ptw_req_bits_bits_need_gpa,0,0);
    VL_IN8(&io_ptw_resp_valid,0,0);
    VL_IN8(&io_ptw_resp_bits_ae_ptw,0,0);
    VL_IN8(&io_ptw_resp_bits_ae_final,0,0);
    VL_IN8(&io_ptw_resp_bits_pf,0,0);
    VL_IN8(&io_ptw_resp_bits_gf,0,0);
    VL_IN8(&io_ptw_resp_bits_hr,0,0);
    VL_IN8(&io_ptw_resp_bits_hw,0,0);
    VL_IN8(&io_ptw_resp_bits_hx,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_d,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_a,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_g,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_u,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_x,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_w,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_r,0,0);
    VL_IN8(&io_ptw_resp_bits_pte_v,0,0);
    VL_IN8(&io_ptw_resp_bits_level,1,0);
    VL_IN8(&io_ptw_resp_bits_homogeneous,0,0);
    VL_IN8(&io_ptw_ptbr_mode,3,0);
    VL_IN8(&io_ptw_status_debug,0,0);
    VL_IN8(&io_ptw_status_prv,1,0);
    VL_IN8(&io_ptw_pmp_0_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_0_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_0_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_0_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_0_cfg_r,0,0);
    VL_IN8(&io_ptw_pmp_1_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_1_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_1_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_1_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_1_cfg_r,0,0);
    VL_IN8(&io_ptw_pmp_2_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_2_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_2_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_2_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_2_cfg_r,0,0);
    VL_IN8(&io_ptw_pmp_3_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_3_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_3_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_3_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_3_cfg_r,0,0);
    VL_IN8(&io_ptw_pmp_4_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_4_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_4_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_4_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_4_cfg_r,0,0);
    VL_IN8(&io_ptw_pmp_5_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_5_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_5_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_5_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_5_cfg_r,0,0);
    VL_IN8(&io_ptw_pmp_6_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_6_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_6_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_6_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_6_cfg_r,0,0);
    VL_IN8(&io_ptw_pmp_7_cfg_l,0,0);
    VL_IN8(&io_ptw_pmp_7_cfg_a,1,0);
    VL_IN8(&io_ptw_pmp_7_cfg_x,0,0);
    VL_IN8(&io_ptw_pmp_7_cfg_w,0,0);
    VL_IN8(&io_ptw_pmp_7_cfg_r,0,0);
    VL_IN8(&cpu_id_ready,0,0);
    VL_OUT8(&ibuf_btb_resp_entry,4,0);
    VL_OUT8(&ibuf_btb_resp_bht_history,7,0);
    VL_OUT8(&ibuf_inst_0_valid,0,0);
    VL_OUT8(&ibuf_inst_0_bits_xcpt0_pf_inst,0,0);
    VL_OUT8(&ibuf_inst_0_bits_xcpt0_gf_inst,0,0);
    VL_OUT8(&ibuf_inst_0_bits_xcpt0_ae_inst,0,0);
    VL_OUT8(&ibuf_inst_0_bits_xcpt1_pf_inst,0,0);
    VL_OUT8(&ibuf_inst_0_bits_xcpt1_gf_inst,0,0);
    VL_OUT8(&ibuf_inst_0_bits_xcpt1_ae_inst,0,0);
    VL_OUT8(&ibuf_inst_0_bits_replay,0,0);
    VL_OUT8(&ibuf_inst_0_bits_rvc,0,0);
    VL_OUT8(&ibuf_inst_0_bits_inst_rd,4,0);
    VL_OUT8(&ibuf_inst_0_bits_inst_rs1,4,0);
    VL_OUT8(&ibuf_inst_0_bits_inst_rs2,4,0);
    VL_OUT8(&ibuf_inst_0_bits_inst_rs3,4,0);
    VL_OUT(&io_cpu_resp_bits_data,31,0);
    VL_OUT(&io_ptw_req_bits_bits_addr,26,0);
    VL_OUT(&ibuf_inst_0_bits_inst_bits,31,0);
    VL_OUT(&ibuf_inst_0_bits_raw,31,0);
    VL_OUT64(&auto_icache_master_out_a_bits_address,34,0);
    VL_IN64(&auto_icache_master_out_d_bits_data,63,0);
    VL_IN64(&io_cpu_req_bits_pc,39,0);
    VL_IN64(&io_cpu_sfence_bits_addr,38,0);
    VL_OUT64(&io_cpu_resp_bits_pc,39,0);
    VL_IN64(&io_cpu_btb_update_bits_pc,38,0);
    VL_IN64(&io_cpu_btb_update_bits_br_pc,38,0);
    VL_IN64(&io_cpu_bht_update_bits_pc,38,0);
    VL_IN64(&io_ptw_resp_bits_pte_ppn,43,0);
    VL_IN64(&io_ptw_pmp_0_addr,32,0);
    VL_IN64(&io_ptw_pmp_0_mask,34,0);
    VL_IN64(&io_ptw_pmp_1_addr,32,0);
    VL_IN64(&io_ptw_pmp_1_mask,34,0);
    VL_IN64(&io_ptw_pmp_2_addr,32,0);
    VL_IN64(&io_ptw_pmp_2_mask,34,0);
    VL_IN64(&io_ptw_pmp_3_addr,32,0);
    VL_IN64(&io_ptw_pmp_3_mask,34,0);
    VL_IN64(&io_ptw_pmp_4_addr,32,0);
    VL_IN64(&io_ptw_pmp_4_mask,34,0);
    VL_IN64(&io_ptw_pmp_5_addr,32,0);
    VL_IN64(&io_ptw_pmp_5_mask,34,0);
    VL_IN64(&io_ptw_pmp_6_addr,32,0);
    VL_IN64(&io_ptw_pmp_6_mask,34,0);
    VL_IN64(&io_ptw_pmp_7_addr,32,0);
    VL_IN64(&io_ptw_pmp_7_mask,34,0);
    VL_IN64(&io_ptw_customCSRs_csrs_0_value,63,0);
    VL_OUT64(&ibuf_pc,39,0);

    // CELLS
    // Public to allow access to /* verilator public */ items.
    // Otherwise the application code can consider these internals.

    // Root instance pointer to allow access to model internals,
    // including inlined /* verilator public_flat_* */ items.
    VIrisRocketFrontendPeer___024root* const rootp;

    // CONSTRUCTORS
    /// Construct the model; called by application code
    /// If contextp is null, then the model will use the default global context
    /// If name is "", then makes a wrapper with a
    /// single model invisible with respect to DPI scope names.
    explicit VIrisRocketFrontendPeer(VerilatedContext* contextp, const char* name = "TOP");
    explicit VIrisRocketFrontendPeer(const char* name = "TOP");
    /// Destroy the model; called (often implicitly) by application code
    virtual ~VIrisRocketFrontendPeer();
  private:
    VL_UNCOPYABLE(VIrisRocketFrontendPeer);  ///< Copying not allowed

  public:
    // API METHODS
    /// Evaluate the model.  Application must call when inputs change.
    void eval() { eval_step(); }
    /// Evaluate when calling multiple units/models per time step.
    void eval_step();
    /// Evaluate at end of a timestep for tracing, when using eval_step().
    /// Application must call after all eval() and before time changes.
    void eval_end_step() {}
    /// Simulation complete, run final blocks.  Application must call on completion.
    void final();
    /// Are there scheduled events to handle?
    bool eventsPending();
    /// Returns time at next time slot. Aborts if !eventsPending()
    uint64_t nextTimeSlot();
    /// Trace signals in the model; called by application code
    void trace(VerilatedTraceBaseC* tfp, int levels, int options = 0) { contextp()->trace(tfp, levels, options); }
    /// Retrieve name of this model instance (as passed to constructor).
    const char* name() const;

    // Abstract methods from VerilatedModel
    const char* hierName() const override final;
    const char* modelName() const override final;
    unsigned threads() const override final;
    /// Prepare for cloning the model at the process level (e.g. fork in Linux)
    /// Release necessary resources. Called before cloning.
    void prepareClone() const;
    /// Re-init after cloning the model at the process level (e.g. fork in Linux)
    /// Re-allocate necessary resources. Called after cloning.
    void atClone() const;
  private:

    // Internal functions - the model's evaluation entry points
    void evalBegin() override final;
    void evalEnd() override final;
    void evalStatic() override final;
    void evalInitial() override final;
    bool evalStl(bool firstIteration) override final;
    void evalSample() override final;
    bool evalIco(bool firstIteration) override final;
    bool evalAct() override final;
    bool evalInact() override final;
    bool evalNba() override final;
    bool evalObs() override final;
    bool evalReact() override final;
    void evalPostponed() override final;
    void evalFinal() override final;
    void dumpTriggersStl() override final;
    void dumpTriggersIco() override final;
    void dumpTriggersAct() override final;
    void dumpTriggersNba() override final;
    void dumpTriggersObs() override final;
    void dumpTriggersReact() override final;

    // Internal functions - trace registration
    void traceBaseModel(VerilatedTraceBaseC* tfp, int levels, int options);
};

#endif  // guard
