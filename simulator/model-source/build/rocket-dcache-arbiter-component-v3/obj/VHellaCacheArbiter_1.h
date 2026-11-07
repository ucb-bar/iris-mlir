// Verilated -*- C++ -*-
// DESCRIPTION: Verilator output: Primary model header
//
// This header should be included by all source files instantiating the design.
// The class here is then constructed to instantiate the design.
// See the Verilator manual for examples.

#ifndef VERILATED_VHELLACACHEARBITER_1_H_
#define VERILATED_VHELLACACHEARBITER_1_H_  // guard

#include "verilated.h"

class VHellaCacheArbiter_1__Syms;
class VHellaCacheArbiter_1___024root;

// This class is the main interface to the Verilated model
class alignas(VL_CACHE_LINE_BYTES) VHellaCacheArbiter_1 VL_NOT_FINAL : public VerilatedModel {
    friend class VHellaCacheArbiter_1__Syms;
  private:
    // Symbol table holding complete model state (owned by this class)
    VHellaCacheArbiter_1__Syms* const vlSymsp;
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
    VL_OUT8(&io_requestor_0_req_ready,0,0);
    VL_IN8(&io_requestor_0_req_valid,0,0);
    VL_IN8(&io_requestor_0_s1_kill,0,0);
    VL_OUT8(&io_requestor_0_s2_nack,0,0);
    VL_OUT8(&io_requestor_0_resp_valid,0,0);
    VL_OUT8(&io_requestor_0_s2_xcpt_ae_ld,0,0);
    VL_OUT8(&io_requestor_1_req_ready,0,0);
    VL_IN8(&io_requestor_1_req_valid,0,0);
    VL_IN8(&io_requestor_1_req_bits_tag,7,0);
    VL_IN8(&io_requestor_1_req_bits_cmd,4,0);
    VL_IN8(&io_requestor_1_req_bits_size,2,0);
    VL_IN8(&io_requestor_1_req_bits_signed,0,0);
    VL_IN8(&io_requestor_1_req_bits_dprv,1,0);
    VL_IN8(&io_requestor_1_req_bits_dv,0,0);
    VL_IN8(&io_requestor_1_s1_kill,0,0);
    VL_OUT8(&io_requestor_1_s2_nack,0,0);
    VL_OUT8(&io_requestor_1_resp_valid,0,0);
    VL_OUT8(&io_requestor_1_resp_bits_tag,7,0);
    VL_OUT8(&io_requestor_1_resp_bits_size,2,0);
    VL_OUT8(&io_requestor_1_resp_bits_replay,0,0);
    VL_OUT8(&io_requestor_1_resp_bits_has_data,0,0);
    VL_OUT8(&io_requestor_1_replay_next,0,0);
    VL_OUT8(&io_requestor_1_s2_xcpt_ma_ld,0,0);
    VL_OUT8(&io_requestor_1_s2_xcpt_ma_st,0,0);
    VL_OUT8(&io_requestor_1_s2_xcpt_pf_ld,0,0);
    VL_OUT8(&io_requestor_1_s2_xcpt_pf_st,0,0);
    VL_OUT8(&io_requestor_1_s2_xcpt_ae_ld,0,0);
    VL_OUT8(&io_requestor_1_s2_xcpt_ae_st,0,0);
    VL_OUT8(&io_requestor_1_ordered,0,0);
    VL_OUT8(&io_requestor_1_store_pending,0,0);
    VL_OUT8(&io_requestor_1_perf_release,0,0);
    VL_OUT8(&io_requestor_1_perf_grant,0,0);
    VL_OUT8(&io_requestor_2_req_ready,0,0);
    VL_IN8(&io_requestor_2_req_valid,0,0);
    VL_IN8(&io_requestor_2_req_bits_tag,7,0);
    VL_IN8(&io_requestor_2_req_bits_cmd,4,0);
    VL_IN8(&io_requestor_2_req_bits_size,2,0);
    VL_IN8(&io_requestor_2_req_bits_signed,0,0);
    VL_IN8(&io_requestor_2_req_bits_dprv,1,0);
    VL_IN8(&io_requestor_2_req_bits_dv,0,0);
    VL_IN8(&io_requestor_2_req_bits_phys,0,0);
    VL_IN8(&io_requestor_2_req_bits_no_alloc,0,0);
    VL_IN8(&io_requestor_2_req_bits_no_xcpt,0,0);
    VL_OUT8(&io_requestor_2_s2_nack,0,0);
    VL_OUT8(&io_requestor_2_resp_valid,0,0);
    VL_OUT8(&io_requestor_2_resp_bits_tag,7,0);
    VL_OUT8(&io_requestor_2_s2_xcpt_ma_ld,0,0);
    VL_OUT8(&io_requestor_2_s2_xcpt_ma_st,0,0);
    VL_OUT8(&io_requestor_2_s2_xcpt_pf_ld,0,0);
    VL_OUT8(&io_requestor_2_s2_xcpt_pf_st,0,0);
    VL_OUT8(&io_requestor_2_s2_xcpt_ae_ld,0,0);
    VL_OUT8(&io_requestor_2_s2_xcpt_ae_st,0,0);
    VL_IN8(&io_mem_req_ready,0,0);
    VL_OUT8(&io_mem_req_valid,0,0);
    VL_OUT8(&io_mem_req_bits_tag,7,0);
    VL_OUT8(&io_mem_req_bits_cmd,4,0);
    VL_OUT8(&io_mem_req_bits_size,2,0);
    VL_OUT8(&io_mem_req_bits_signed,0,0);
    VL_OUT8(&io_mem_req_bits_dprv,1,0);
    VL_OUT8(&io_mem_req_bits_dv,0,0);
    VL_OUT8(&io_mem_req_bits_phys,0,0);
    VL_OUT8(&io_mem_req_bits_no_alloc,0,0);
    VL_OUT8(&io_mem_req_bits_no_xcpt,0,0);
    VL_OUT8(&io_mem_s1_kill,0,0);
    VL_IN8(&io_mem_s2_nack,0,0);
    VL_IN8(&io_mem_resp_valid,0,0);
    VL_IN8(&io_mem_resp_bits_tag,7,0);
    VL_IN8(&io_mem_resp_bits_size,2,0);
    VL_IN8(&io_mem_resp_bits_replay,0,0);
    VL_IN8(&io_mem_resp_bits_has_data,0,0);
    VL_IN8(&io_mem_replay_next,0,0);
    VL_IN8(&io_mem_s2_xcpt_ma_ld,0,0);
    VL_IN8(&io_mem_s2_xcpt_ma_st,0,0);
    VL_IN8(&io_mem_s2_xcpt_pf_ld,0,0);
    VL_IN8(&io_mem_s2_xcpt_pf_st,0,0);
    VL_IN8(&io_mem_s2_xcpt_ae_ld,0,0);
    VL_IN8(&io_mem_s2_xcpt_ae_st,0,0);
    VL_IN8(&io_mem_ordered,0,0);
    VL_IN8(&io_mem_store_pending,0,0);
    VL_IN8(&io_mem_perf_release,0,0);
    VL_IN8(&io_mem_perf_grant,0,0);
    VL_OUTW(&io_requestor_0_resp_bits_data,255,0,8);
    VL_INW(&io_requestor_1_s1_data_data,255,0,8);
    VL_OUTW(&io_requestor_1_resp_bits_data,255,0,8);
    VL_OUTW(&io_requestor_1_resp_bits_data_word_bypass,255,0,8);
    VL_IN(&io_requestor_2_req_bits_mask,31,0);
    VL_INW(&io_requestor_2_s1_data_data,255,0,8);
    VL_IN(&io_requestor_2_s1_data_mask,31,0);
    VL_OUTW(&io_requestor_2_resp_bits_data_raw,255,0,8);
    VL_OUT(&io_mem_req_bits_mask,31,0);
    VL_OUTW(&io_mem_s1_data_data,255,0,8);
    VL_OUT(&io_mem_s1_data_mask,31,0);
    VL_INW(&io_mem_resp_bits_data,255,0,8);
    VL_INW(&io_mem_resp_bits_data_word_bypass,255,0,8);
    VL_INW(&io_mem_resp_bits_data_raw,255,0,8);
    VL_IN64(&io_requestor_0_req_bits_addr,39,0);
    VL_IN64(&io_requestor_1_req_bits_addr,39,0);
    VL_IN64(&io_requestor_2_req_bits_addr,39,0);
    VL_OUT64(&io_mem_req_bits_addr,39,0);

    // CELLS
    // Public to allow access to /* verilator public */ items.
    // Otherwise the application code can consider these internals.

    // Root instance pointer to allow access to model internals,
    // including inlined /* verilator public_flat_* */ items.
    VHellaCacheArbiter_1___024root* const rootp;

    // CONSTRUCTORS
    /// Construct the model; called by application code
    /// If contextp is null, then the model will use the default global context
    /// If name is "", then makes a wrapper with a
    /// single model invisible with respect to DPI scope names.
    explicit VHellaCacheArbiter_1(VerilatedContext* contextp, const char* name = "TOP");
    explicit VHellaCacheArbiter_1(const char* name = "TOP");
    /// Destroy the model; called (often implicitly) by application code
    virtual ~VHellaCacheArbiter_1();
  private:
    VL_UNCOPYABLE(VHellaCacheArbiter_1);  ///< Copying not allowed

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
