#include <pybind11/pybind11.h>
#include "job_mapper.h"

namespace py = pybind11;


PYBIND11_MODULE(smtcheck_native, m) {
    m.doc() = "SMTcheck native extension";

    // Delegate binding registration to each module
    bind_job_mapper(m);
}